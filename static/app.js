/**
 * app.js
 * -------
 * Handles all frontend logic:
 * - Login / logout
 * - Rendering the ID card and quick actions
 * - Sending messages and displaying replies
 * - Typing indicator, timestamps, markdown rendering
 * - Lottie bot animations (login page, sidebar, chat avatars)
 */

// =========================================================
// STATE
// =========================================================
let userRole = null;
let messageCount = 0;
// Guards handleSessionExpired() against firing more than once if multiple
// in-flight requests all come back 401 around the same time - reset to
// false again once a fresh login succeeds (see showChatPage()).
let sessionExpiredHandled = false;


// =========================================================
// LOTTIE BOT ANIMATIONS
// The mascot JSON is ~180KB, so we fetch and parse it ONCE and reuse the
// parsed object for every instance (passing `animationData`). Loading it
// per-bubble via `path` would re-parse the whole file for every message.
// =========================================================
const BOT_LOTTIE_URL = "/static/chatbot.json";
let botLottieData = null;          // cached parsed JSON
let botLottieLoading = null;       // in-flight fetch, so we only request once
let lottieInstances = [];          // kept so we can destroy them on clearChat()
let lottieIdCounter = 0;

function loadBotLottieData() {
    if (botLottieData) return Promise.resolve(botLottieData);
    if (botLottieLoading) return botLottieLoading;

    botLottieLoading = fetch(BOT_LOTTIE_URL)
        .then(res => res.json())
        .then(data => {
            botLottieData = data;
            return data;
        })
        .catch(() => null);   // animation is decorative - never break the UI over it

    return botLottieLoading;
}

/**
 * Mount the bot mascot into a container element.
 * Safe to call before the JSON has downloaded - it waits, then renders.
 */
function mountBotLottie(container) {
    if (!container || typeof lottie === "undefined") return;

    loadBotLottieData().then(data => {
        if (!data || !container.isConnected) return;
        const anim = lottie.loadAnimation({
            container: container,
            renderer: "svg",
            loop: true,
            autoplay: true,
            animationData: data
        });
        lottieInstances.push({ anim, container });
    });
}

/**
 * Read a mascot size from the CSS variables in index.html.
 * The variable stays the single place to tune sizes, but we fall back to a
 * hardcoded px value if the stylesheet is missing or stale - without a size
 * the Lottie SVG falls back to its intrinsic 500x500 viewBox and renders
 * enormously, which is much worse than being slightly the wrong size.
 */
function botLottieSize(cssVar, fallback) {
    const value = getComputedStyle(document.documentElement)
        .getPropertyValue(cssVar).trim();
    return value || fallback;
}

/** Destroy animations whose container is no longer in the document. */
function cleanupDetachedLottie() {
    lottieInstances = lottieInstances.filter(({ anim, container }) => {
        if (!container.isConnected) {
            anim.destroy();
            return false;
        }
        return true;
    });
}

const subGreetings = [
    "What would you like to know today?",
    "How can I help you today?",
    "Ready to help — just ask!",
    "Your school assistant is here.",
    "Got questions? I've got answers.",
    "Ask me anything about your school info.",
    "What's on your mind?",
    "Here to make school life easier.",
];

const quickActions = {
    student: [
        { label: "My Attendance",  msg: "what is my attendance" },
        { label: "Upcoming Exams", msg: "when are my exams" },
        { label: "My Timetable",   msg: "show me my timetable" },
        { label: "Fee Status",     msg: "what is my fee status" },
    ],
    teacher: [
        { label: "My Schedule",    msg: "show me my timetable" },
        { label: "My Periods",     msg: "how many periods do I have" },
        { label: "My Classes",     msg: "which classes do I teach" },
    ],
    principal: [
        { label: "Total Students", msg: "how many students are there" },
        { label: "Total Teachers", msg: "how many teachers do we have" },
        { label: "Class Breakdown", msg: "students per class" },
    ]
};


// =========================================================
// LOGIN
// =========================================================
async function handleLogin() {
    const username = document.getElementById("login-username").value.trim();
    const password = document.getElementById("login-password").value;
    const errorEl = document.getElementById("login-error");
    const btn = document.getElementById("login-btn");

    errorEl.classList.add("hidden");
    setLoginLoading(btn, true);

    try {
        const res = await fetch("/api/login", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ username, password })
        });
        const data = await res.json();

        // Reset the "Signing in..." spinner state before branching, so the
        // lockout branch below can re-disable the button without it being
        // undone by this unconditional reset.
        setLoginLoading(btn, false);

        if (data.success) {
            userRole = data.role;
            showChatPage(data.profile);
        } else if (res.status === 429 && data.retry_after) {
            startLoginLockoutCountdown(data.retry_after);
        } else {
            errorEl.textContent = data.error;
            errorEl.classList.remove("hidden");
        }
        return;
    } catch (e) {
        errorEl.textContent = "Connection error. Is the server running?";
        errorEl.classList.remove("hidden");
    }

    setLoginLoading(btn, false);
}

// Tracks the running countdown's interval ID so a new lockout response
// (or another handleLogin call) can cancel a previous countdown instead of
// stacking multiple timers.
let _loginLockoutInterval = null;

/**
 * Shows a live minutes:seconds countdown in the login error box and keeps
 * the sign-in button disabled until it reaches zero, at which point the
 * button re-enables itself automatically - no page refresh needed.
 */
function startLoginLockoutCountdown(seconds) {
    const errorEl = document.getElementById("login-error");
    const btn = document.getElementById("login-btn");

    if (_loginLockoutInterval) {
        clearInterval(_loginLockoutInterval);
        _loginLockoutInterval = null;
    }

    let remaining = seconds;

    const render = () => {
        const mins = Math.floor(remaining / 60);
        const secs = remaining % 60;
        const timeStr = `${mins}:${String(secs).padStart(2, "0")}`;
        errorEl.textContent = `Too many failed attempts. Please try again in ${timeStr}.`;
        errorEl.classList.remove("hidden");
    };

    render();
    btn.disabled = true;

    _loginLockoutInterval = setInterval(() => {
        remaining -= 1;
        if (remaining <= 0) {
            clearInterval(_loginLockoutInterval);
            _loginLockoutInterval = null;
            errorEl.classList.add("hidden");
            btn.disabled = false;
            return;
        }
        render();
    }, 1000);
}

/**
 * Toggle the sign-in button between its normal and loading state.
 * Loading shows a small spinning circle next to "Signing in..." and
 * disables the button so it can't be double-submitted.
 */
function setLoginLoading(btn, loading) {
    const textEl = document.getElementById("login-btn-text");
    const existingSpinner = document.getElementById("login-spinner");

    btn.disabled = loading;

    if (loading) {
        textEl.textContent = "Signing in...";
        if (!existingSpinner) {
            const spinner = document.createElement("span");
            spinner.id = "login-spinner";
            spinner.className = "btn-spinner";
            btn.insertBefore(spinner, textEl);
        }
    } else {
        textEl.textContent = "Sign In";
        if (existingSpinner) existingSpinner.remove();
    }
}

// Allow Enter key to submit login
document.addEventListener("DOMContentLoaded", () => {
    document.getElementById("login-password").addEventListener("keydown", (e) => {
        if (e.key === "Enter") handleLogin();
    });
    document.getElementById("login-username").addEventListener("keydown", (e) => {
        if (e.key === "Enter") handleLogin();
    });

    // Login page mascot - the element exists and is visible right away.
    mountBotLottie(document.getElementById("login-lottie"));

    restoreSession();
});

// The session cookie survives a page refresh on its own - the frontend just
// needs to check for it and skip straight to the chat page instead of
// always showing the login screen first.
async function restoreSession() {
    try {
        const res = await fetch("/api/me");
        const data = await res.json();
        if (data.logged_in) {
            userRole = data.role;
            showChatPage(data.profile);
        }
    } catch (e) {
        // Not logged in, or server unreachable - stay on the login page.
    }
}


// =========================================================
// SHOW CHAT PAGE
// =========================================================
function showChatPage(profile) {
    // A fresh, successful session is in place again - re-arm the
    // session-expiry guard so a LATER expiry can trigger it again.
    sessionExpiredHandled = false;

    document.getElementById("login-page").classList.add("hidden");
    document.getElementById("chat-page").classList.remove("hidden");
    document.getElementById("chat-page").classList.add("flex");

    // Sidebar mascot - mounted here rather than on DOMContentLoaded because
    // the sidebar lives inside #chat-page, which is display:none until now.
    const sidebarLottie = document.getElementById("sidebar-lottie");
    if (sidebarLottie && !sidebarLottie.hasChildNodes()) {
        mountBotLottie(sidebarLottie);
    }

    // Set greeting
    const hour = new Date().getHours();
    const timeGreeting = hour < 12 ? "Good morning" : hour < 17 ? "Good afternoon" : "Good evening";
    const firstName = profile.name.split(" ")[0];
    const sub = subGreetings[Math.floor(Math.random() * subGreetings.length)];

    document.getElementById("greeting-time").textContent = timeGreeting;
    document.getElementById("greeting-name").textContent = `${firstName} 👋`;
    // Nova's introduction prefixed onto the existing rotating sub-greeting -
    // this is the first thing the assistant "says" before any chat happens.
    // Purely a text change: subGreetings itself is untouched.
    document.getElementById("greeting-sub").textContent = `Hi, I'm Nova! ${sub}`;

    // Build ID card
    buildIDCard(profile);

    // Build quick actions
    buildQuickActions();

    // Focus input
    document.getElementById("chat-input").focus();
}


// =========================================================
// SIDEBAR (collapsible on mobile/tablet, below the 768px breakpoint)
// On desktop the sidebar is always visible via CSS (md:translate-x-0) and
// these functions have no visible effect there - they only matter for the
// fixed-overlay behavior below the breakpoint.
// =========================================================
function openSidebar() {
    document.getElementById("sidebar").classList.add("sidebar-open");
    document.getElementById("sidebar-backdrop").classList.remove("opacity-0", "pointer-events-none");
    document.getElementById("hamburger-btn").classList.add("hidden");
}

function closeSidebar() {
    document.getElementById("sidebar").classList.remove("sidebar-open");
    document.getElementById("sidebar-backdrop").classList.add("opacity-0", "pointer-events-none");
    document.getElementById("hamburger-btn").classList.remove("hidden");
}

// The CSS md: breakpoints already guarantee the sidebar renders correctly
// at every width on their own - this just resets the mobile "open" state
// when the window crosses into desktop layout (e.g. widening a window, or
// rotating a tablet), so the sidebar doesn't stay stuck in its mobile-open
// state if the window is later resized back down below 768px.
window.addEventListener("resize", () => {
    if (window.innerWidth >= 768) {
        closeSidebar();
    }
});

function buildIDCard(profile) {
    const nameEl = document.getElementById("card-name");
    const subEl  = document.getElementById("card-sub");
    const statsEl = document.getElementById("card-stats");

    if (userRole === "student") {
        nameEl.textContent = profile.name;
        subEl.textContent  = `Class ${profile.class}  ·  Roll No. ${profile.roll_no}`;

        const attColor = profile.attendance >= 75 ? "#4ade80" : "#f87171";
        const feesIcon = profile.fees === "paid" ? "✓" : "!";
        const feesColor = profile.fees === "paid" ? "#4ade80" : "#f87171";

        statsEl.innerHTML = `
            <div class="flex-1 bg-white bg-opacity-20 rounded-xl p-2 text-center">
                <div class="text-base font-bold" style="color:${attColor}">${profile.attendance}%</div>
                <div class="text-xs opacity-75 uppercase tracking-wide">Attendance</div>
            </div>
            <div class="flex-1 bg-white bg-opacity-20 rounded-xl p-2 text-center">
                <div class="text-base font-bold" style="color:${feesColor}">${feesIcon}</div>
                <div class="text-xs opacity-75 uppercase tracking-wide">Fees</div>
            </div>
        `;

        // Update card label
        document.querySelector("#id-card .text-xs").textContent = "Student ID";

    } else if (userRole === "teacher") {
        nameEl.textContent = profile.name;
        subEl.textContent  = `${profile.subject} Teacher`;
        statsEl.innerHTML  = `
            <div class="flex-1 bg-white bg-opacity-20 rounded-xl p-2 text-center">
                <div class="text-base font-bold">👨‍🏫</div>
                <div class="text-xs opacity-75 uppercase tracking-wide">Faculty</div>
            </div>
        `;
        document.querySelector("#id-card .text-xs").textContent = "Teacher ID";

    } else {
        nameEl.textContent = "Principal";
        subEl.textContent  = "Administration";
        statsEl.innerHTML  = `
            <div class="flex-1 bg-white bg-opacity-20 rounded-xl p-2 text-center">
                <div class="text-base font-bold">🏫</div>
                <div class="text-xs opacity-75 uppercase tracking-wide">Admin</div>
            </div>
        `;
        document.querySelector("#id-card .text-xs").textContent = "Principal";
    }
}

function buildQuickActions() {
    const container = document.getElementById("quick-actions");
    const actions = quickActions[userRole] || [];

    // Tailwind gotcha, found via a mobile audit: the CDN/Play build's JIT
    // compiler only reliably generates CSS for utility classes it has seen
    // SOMEWHERE in the page - including ones injected dynamically after
    // load, AS LONG AS that exact class also appears at least once in the
    // static HTML (templates/index.html). A class used ONLY here, inside a
    // JS template string that never runs until buildQuickActions() fires,
    // was silently generating NO CSS at all - these buttons rendered with
    // zero padding (px-3.5/py-2.5 previously) even though the class names
    // were correctly present in the DOM. Fixed by switching to px-4/py-3,
    // which are already used elsewhere in the static HTML. Keep this in
    // mind for any NEW class added only in JS-built markup like this.
    container.innerHTML = actions.map(action => `
        <button
            onclick="sendQuick('${action.msg}')"
            class="chip w-full text-left text-sm text-gray-700 bg-white border border-gray-200
                   rounded-xl px-4 py-3
                   transition-all duration-150 font-medium"
        >
            ${action.label}
        </button>
    `).join("");
}


// =========================================================
// SESSION EXPIRY
// /api/chat checks the Flask session BEFORE deciding NLP vs Gemini lane
// (see app.py), so a 401 from it can only ever come back as a normal JSON
// response, never mid-SSE-stream - by the time sendMessage() would hand a
// response to handleStreamingReply(), a 401 has already been ruled out.
// One check, made right after the fetch resolves and before the
// streaming/non-streaming branch, therefore covers both lanes.
//
// Previously a 401 here fell through to the ordinary reply-rendering path
// and showed the raw {"error":"Not logged in."} JSON inside a chat bubble,
// leaving the user stuck typing into a chat that could never work again
// without a manual refresh.
// =========================================================
function handleSessionExpired() {
    if (sessionExpiredHandled) return;
    sessionExpiredHandled = true;

    removeTypingBubble();
    clearChat();
    userRole = null;

    document.getElementById("chat-page").classList.add("hidden");
    document.getElementById("chat-page").classList.remove("flex");
    // Same login screen shown on initial page load / a failed restoreSession()
    // - no separate "expired" UI to build or keep in sync with it.
    document.getElementById("login-page").classList.remove("hidden");

    const errorEl = document.getElementById("login-error");
    errorEl.textContent = "Your session has expired, please log in again.";
    errorEl.classList.remove("hidden");
}


// =========================================================
// CHAT
// =========================================================
function sendQuick(msg) {
    document.getElementById("chat-input").value = msg;
    sendMessage();
}

async function sendMessage() {
    const input = document.getElementById("chat-input");
    const message = input.value.trim();
    if (!message) return;

    input.value = "";
    autoResize(input);

    // Hide greeting on first message
    if (messageCount === 0) {
        document.getElementById("greeting").style.display = "none";
    }
    messageCount++;

    // Show user message
    appendMessage("user", message);

    // Show typing bubble
    showTypingBubble();

    try {
        const res = await fetch("/api/chat", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ message })
        });

        if (res.status === 401) {
            handleSessionExpired();
            return;
        }

        // NLP-lane answers are instant plain JSON (unchanged). Gemini-lane
        // answers come back as an SSE stream instead - tell the two apart
        // by Content-Type rather than assuming, since either can come back
        // from this same endpoint depending on how the question routed.
        const contentType = res.headers.get("Content-Type") || "";

        if (contentType.includes("text/event-stream")) {
            await handleStreamingReply(res);
        } else {
            const data = await res.json();
            removeTypingBubble();
            appendMessage("bot", data.reply || data.error || "Something went wrong.");
        }

    } catch (e) {
        removeTypingBubble();
        appendMessage("bot", "⚠️ Connection error. Please check that the server is running.");
    }
}

/**
 * Read an SSE stream from /api/chat (the Gemini lane) and render it into a
 * bot bubble incrementally, so text appears as it arrives instead of all
 * at once at the end.
 *
 * The typing bubble is removed the moment the FIRST real chunk arrives
 * (not after the stream finishes) - a cache hit still comes through this
 * same path as a single chunk, so it disappears essentially instantly;
 * a real Gemini call disappears the moment the first words are ready.
 */
async function handleStreamingReply(res) {
    const reader = res.body.getReader();
    const decoder = new TextDecoder();

    let buffer = "";     // holds a partial SSE message split across reads
    let fullText = "";   // accumulated answer text, re-rendered each chunk
    let bubble = null;   // created lazily on the first real chunk
    let typingRemoved = false;

    while (true) {
        const { value, done } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });

        // SSE messages are separated by a blank line ("\n\n"). The last
        // piece after splitting may be an incomplete message still being
        // received, so keep it in the buffer for the next read.
        const messages = buffer.split("\n\n");
        buffer = messages.pop();

        for (const raw of messages) {
            const line = raw.trim();
            if (!line.startsWith("data:")) continue;

            const payload = line.slice("data:".length).trim();
            if (payload === "[DONE]") continue;

            let parsed;
            try {
                parsed = JSON.parse(payload);
            } catch (e) {
                continue; // ignore a malformed chunk rather than crash the chat
            }

            const chunkText = parsed.chunk || "";
            if (!chunkText) continue;

            if (!typingRemoved) {
                removeTypingBubble();
                typingRemoved = true;
            }
            if (!bubble) {
                bubble = buildMessageWrapper("bot");
            }

            // Re-parsing the whole accumulated text on each chunk (rather
            // than trying to append raw HTML) is what keeps markdown -
            // bold, bullet points - rendering correctly once the stream
            // finishes, even though it may look slightly unformatted for
            // an instant mid-stream.
            fullText += chunkText;
            bubble.innerHTML = marked.parse(fullText);

            const container = document.getElementById("chat-messages");
            container.scrollTop = container.scrollHeight;
        }
    }

    // Safety net: gemini_answer_stream() always yields at least one chunk
    // (a real answer or a fallback message), but if the connection dropped
    // before anything arrived, don't leave the typing bubble on screen forever.
    if (!typingRemoved) {
        removeTypingBubble();
    }
}

/**
 * Build and attach an empty message bubble (avatar + bubble + timestamp)
 * and return the bubble element for the caller to fill in.
 *
 * Split out of appendMessage() so the streaming path can create a bubble
 * up front (on the first chunk) and then update its contents repeatedly as
 * more chunks arrive, instead of only ever being able to set text once.
 */
function buildMessageWrapper(role) {
    const container = document.getElementById("chat-messages");
    const now = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

    const wrapper = document.createElement("div");
    wrapper.className = `flex flex-col fade-in ${role === "user" ? "items-end" : "items-start"}`;

    const bubble = document.createElement("div");
    // User bubbles are solid brand colour, so their text is white; bot bubbles
    // sit on the light tint and keep dark text.
    bubble.className = `max-w-lg px-4 py-3 text-sm leading-relaxed ${
        role === "user" ? "msg-user text-white" : "msg-bot text-gray-800"
    }`;

    const timestamp = document.createElement("div");
    // gray-600, not the lighter gray-400/300 shades elsewhere in this file
    // originally used - those failed WCAG AA contrast (2.5:1 and 1.5:1
    // against white, need 4.5:1), caught by an axe-core/Lighthouse audit.
    timestamp.className = "text-xs text-gray-600 mt-1 mx-1";
    timestamp.textContent = now;

    // Avatar
    const avatarRow = document.createElement("div");
    avatarRow.className = `flex items-end gap-2 ${role === "user" ? "flex-row-reverse" : "flex-row"}`;

    const avatar = document.createElement("div");
    avatar.className = "rounded-full flex items-center justify-center text-xs flex-shrink-0";

    if (role === "bot") {
        // Animated Lottie mascot instead of a static emoji. Each bubble needs
        // its own container id so multiple animations don't collide.
        // Size comes from --bot-size-chat in index.html, not a hardcoded value here.
        avatar.id = `lottie-bot-${Date.now()}-${lottieIdCounter++}`;
        avatar.classList.add("bot-lottie-chat");
        const size = botLottieSize("--bot-size-chat", "44px");
        avatar.style.width = size;
        avatar.style.height = size;
    } else {
        avatar.classList.add("w-7", "h-7");
        avatar.style.background = "#F4F5FF";
        avatar.textContent = "👤";
    }

    avatarRow.appendChild(avatar);
    avatarRow.appendChild(bubble);

    wrapper.appendChild(avatarRow);
    wrapper.appendChild(timestamp);
    container.appendChild(wrapper);

    // Mount the mascot only after the avatar is attached to the document,
    // otherwise Lottie has no laid-out container to render into.
    if (role === "bot") {
        mountBotLottie(avatar);
    }

    container.scrollTop = container.scrollHeight;
    return bubble;
}

function appendMessage(role, text) {
    const bubble = buildMessageWrapper(role);

    // Render markdown for bot messages (bold, line breaks etc)
    bubble.innerHTML = role === "bot"
        ? marked.parse(text)
        : `<p>${escapeHtml(text)}</p>`;

    const container = document.getElementById("chat-messages");
    container.scrollTop = container.scrollHeight;
}

function showTypingBubble() {
    const container = document.getElementById("chat-messages");

    const wrapper = document.createElement("div");
    wrapper.id = "typing-bubble";
    wrapper.className = "flex items-end gap-2 fade-in";

    // Same animated mascot as a real bot message, so the avatar doesn't
    // visibly swap when the reply lands.
    wrapper.innerHTML = `
        <div id="lottie-bot-typing" class="bot-lottie-chat flex-shrink-0"
             style="width:${botLottieSize("--bot-size-chat", "44px")};height:${botLottieSize("--bot-size-chat", "44px")};"></div>
        <div class="typing-bubble-pulse msg-bot px-4 py-3 flex items-center gap-1.5">
            <span class="typing-dot"></span>
            <span class="typing-dot"></span>
            <span class="typing-dot"></span>
        </div>
    `;

    container.appendChild(wrapper);
    mountBotLottie(document.getElementById("lottie-bot-typing"));
    container.scrollTop = container.scrollHeight;
}

function removeTypingBubble() {
    const bubble = document.getElementById("typing-bubble");
    if (bubble) bubble.remove();
    // Free the typing bubble's mascot - otherwise it keeps animating a
    // detached node for the rest of the session.
    cleanupDetachedLottie();
}

function clearChat() {
    messageCount = 0;
    const container = document.getElementById("chat-messages");
    // Remove all messages but keep the greeting
    container.innerHTML = `
        <div id="greeting" class="flex flex-col items-center justify-center h-full text-center pb-20">
            <div id="greeting-time" class="text-xs font-semibold text-gray-600 uppercase tracking-wider mb-2"></div>
            <h2 id="greeting-name" class="text-4xl font-bold text-gray-900 tracking-tight mb-2"></h2>
            <p id="greeting-sub" class="text-base text-gray-500"></p>
        </div>
    `;
    // Wiping innerHTML detaches every message avatar, so destroy their
    // animations rather than leaving them running in the background.
    cleanupDetachedLottie();

    // Re-set greeting text
    const hour = new Date().getHours();
    document.getElementById("greeting-time").textContent =
        hour < 12 ? "Good morning" : hour < 17 ? "Good afternoon" : "Good evening";
}


// =========================================================
// LOGOUT
// =========================================================
async function handleLogout() {
    await fetch("/api/logout", { method: "POST" });
    location.reload();
}


// =========================================================
// UTILS
// =========================================================
function handleKey(e) {
    // Send on Enter, new line on Shift+Enter
    if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
    }
}

function autoResize(el) {
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 120) + "px";
}

function escapeHtml(text) {
    return text
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}
