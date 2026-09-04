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
// LOTTIE ANIMATIONS
// Each JSON is fetched and parsed ONCE per URL and the parsed object reused
// for every instance of that animation (passing `animationData`). Loading
// per-bubble via `path` would re-parse the whole file every time.
// =========================================================
const BOT_LOTTIE_URL = "/static/chatbot.json";
// Decorative chameleon mascot (sidebar + login card). Has a leftover
// "nwsys.png" image layer and an empty "by <creator>" text layer from its
// source file, but neither renders in any frame - the image's ip/op are
// equal (zero duration, stays display:none) and the text layer's string
// content is "". No stripping needed.
const CHAMELEON_LOTTIE_URL = "/static/Camaleon.json";
const _lottieDataCache = {};      // url -> cached parsed JSON
const _lottieLoadingCache = {};   // url -> in-flight fetch, so each url is only requested once
let lottieInstances = [];         // kept so we can destroy them on clearChat()
let lottieIdCounter = 0;

// chatbot.json's character only occupies ~30%x72% of its native 500x500
// canvas (measured across all 150 idle-loop frames: shapes span roughly
// x:169-320, y:94-452), which made the mascot look tiny in its box.
// Cropping the SVG's viewBox to a square centered on the character, sized
// to contain the whole motion envelope plus a margin, fixes it without a
// bigger container. Not applied to Camaleon.json - its envelope actually
// extends PAST its 1080x1080 canvas (a fly + leaf accent legitimately
// off-canvas), so cropping it would clip real artwork.
const LOTTIE_VIEWBOX_CROPS = {
    [BOT_LOTTIE_URL]: "45 73 400 400",
};

// =========================================================
// SPIDER-MAN EASTER EGG
// Anchored to the persistent main chat area (#spiderman-lottie in
// index.html, a direct child of the flex-1 main-content wrapper, not the
// sidebar). Mounts once per session in showChatPage() and stays active the
// whole session, including after messages are sent - only torn down by
// handleSessionExpired()/handleLogout(), never sendMessage()/clearChat().
// See initSpiderman()/teardownSpiderman() below for the lifecycle.
// =========================================================
const SPIDERMAN_LOTTIE_URL = "/static/animation_spider.json";

// The old crop ("0 -21 32 53") used the full native 0-32 canvas width,
// leaving the character at ~30% of the container's width - a tiny speck
// padded by empty crop space, not a DOM bug (mount/opacity/z-index were
// fine). Re-measured via the SVG transform matrix on the character's <g>
// across every frame: scale (0.0302) and X (10.92) are identical at every
// frame - he only moves vertically. Real footprint on the 336x695 native
// asset: width ~10.15 (x: 10.92-21.08), height ~21 (y: -21 to 21). "8 -23
// 16 46" crops tightly to that. --spiderman-perch-height-* in index.html
// was recomputed to match this crop's 16:46 aspect ratio too - the old
// height assumed 32:53, which let "meet" scaling add more letterboxing.
const SPIDERMAN_VIEWBOX_CROP = "8 -23 16 46";

// Frame ranges, measured from the rendered animation: 0-6 fade in, 6-24 a
// settle wobble, 24-32 the actual drop (y: 0 -> -21). 32-62.33 (the rest
// of the file) is a dead hold - every layer's position/opacity is
// byte-identical at frames 32/40/50/62, no baked-in "spring back up"
// sequence exists. So "retract" plays the drop segment in reverse
// (playSegments([end, start], true)) - the only way to get a retraction
// out of a file that only ever animates downward.
const SPIDERMAN_DROP_SEGMENT = [0, 32];
const SPIDERMAN_LOWEST_FRAME = 32;
const SPIDERMAN_RETRACTED_FRAME = 0;

const SPIDERMAN_IDLE_MS = 30000;       // no typing for this long after he drops -> auto-retract
const SPIDERMAN_REDROP_DELAY_MS = 5000; // after an idle-triggered retract, wait this long before re-checking

let spidermanAnim = null;
let spidermanIdleTimer = null;
let spidermanRedropTimer = null;
let spidermanInputTarget = null;   // the actual <textarea> the listener is attached to, for clean removal
let spidermanInputListener = null;
// True from initSpiderman() start until teardownSpiderman() - guards a
// race where loadLottieData() (async) hasn't resolved yet and
// showChatPage() runs again (e.g. session-expiry-then-relogin), which
// would otherwise start a second concurrent mount. Checked synchronously
// before the fetch even starts; spidermanGeneration below only covers the
// async callback itself.
let spidermanMountStarted = false;
// Bumped by both init and teardown - covers a second race: a genuine
// teardown while an initSpiderman() fetch is still in flight would
// otherwise let that mount finish and leave a live instance ticking after
// the session ended. Each async callback captures the generation at call
// time and bails if it no longer matches.
let spidermanGeneration = 0;
// Kept so clearChat() can rebuild the greeting the same way showChatPage() does.
let currentProfile = null;

// Without explicit state, retractSpiderman() couldn't tell "already
// retracted" from "currently hanging" and called playSegments()
// unconditionally on every 'input' event - forceFlag:true snaps back to
// the segment start first, so every keystroke replayed the whole retract.
//   'idle'      - nothing mounted/dropped yet
//   'dropped'   - hanging at the settled frame, idle timer running
//   'retracted' - pulled back up/out of view, nothing animating
let spidermanState = "idle";

/** Cancel both Spider-Man timers without touching the animation itself. */
function clearSpidermanTimers() {
    if (spidermanIdleTimer) { clearTimeout(spidermanIdleTimer); spidermanIdleTimer = null; }
    if (spidermanRedropTimer) { clearTimeout(spidermanRedropTimer); spidermanRedropTimer = null; }
}

/**
 * Full teardown: cancels timers, detaches the input listener, destroys
 * the Lottie instance. Real boundary is a session end -
 * handleSessionExpired()/handleLogout() - since #spiderman-lottie is
 * persistent and survives sendMessage()/clearChat(). Resetting
 * spidermanMountStarted (not just spidermanAnim) allows a legitimate
 * fresh mount if the user logs back in without a full page reload.
 */
function teardownSpiderman() {
    spidermanGeneration++;   // invalidates any in-flight initSpiderman() mount
    spidermanMountStarted = false;
    spidermanState = "idle";
    clearSpidermanTimers();
    if (spidermanInputTarget && spidermanInputListener) {
        spidermanInputTarget.removeEventListener("input", spidermanInputListener);
    }
    spidermanInputTarget = null;
    spidermanInputListener = null;
    if (spidermanAnim) {
        spidermanAnim.destroy();
        spidermanAnim = null;
    }
    // Container persists in the DOM across the session, so a later fresh
    // mount could otherwise start from a stale "dropped" class.
    const container = document.getElementById("spiderman-lottie");
    if (container) container.classList.remove("spiderman-dropped");
}

function armSpidermanIdleTimer() {
    if (spidermanIdleTimer) clearTimeout(spidermanIdleTimer);
    spidermanIdleTimer = setTimeout(() => {
        spidermanIdleTimer = null;
        retractSpiderman(true);   // idle-triggered - eligible for the 5s re-drop check
    }, SPIDERMAN_IDLE_MS);
}

function dropSpiderman() {
    if (!spidermanAnim) return;
    if (spidermanState === "dropped") return;   // already hanging - nothing to do
    spidermanState = "dropped";
    // The Lottie file's own keyframed motion only moves the character
    // ~25px - the real travel distance is this CSS class toggle (see
    // .spiderman-perch.spiderman-dropped in index.html); playSegments()
    // below just layers his small settle motion on top of it.
    const container = document.getElementById("spiderman-lottie");
    if (container) container.classList.add("spiderman-dropped");
    spidermanAnim.playSegments(SPIDERMAN_DROP_SEGMENT, true);
    armSpidermanIdleTimer();
}

/**
 * @param fromIdleTimeout - true only when the 30s idle timer itself fired.
 * Typing (the 'input' listener) always calls this with false: it should
 * retract him ONCE and stay retracted while the user keeps typing, never
 * queue a re-drop.
 */
function retractSpiderman(fromIdleTimeout) {
    if (!spidermanAnim) return;
    // Any retraction cancels whatever's pending, including a re-drop
    // queued by an earlier idle timeout - so typing during that 5s window
    // correctly cancels the re-drop. No-op if nothing's armed.
    if (spidermanIdleTimer) { clearTimeout(spidermanIdleTimer); spidermanIdleTimer = null; }
    if (spidermanRedropTimer) { clearTimeout(spidermanRedropTimer); spidermanRedropTimer = null; }

    // Only fires on the transition INTO "retracted" - every keystroke
    // after the first is a no-op instead of replaying the animation.
    if (spidermanState !== "retracted") {
        spidermanState = "retracted";
        const container = document.getElementById("spiderman-lottie");
        if (container) container.classList.remove("spiderman-dropped");
        spidermanAnim.playSegments([SPIDERMAN_LOWEST_FRAME, SPIDERMAN_RETRACTED_FRAME], true);
    }

    if (fromIdleTimeout) {
        spidermanRedropTimer = setTimeout(() => {
            spidermanRedropTimer = null;
            const input = document.getElementById("chat-input");
            if (input && input.value.trim() === "") {
                dropSpiderman();
            }
        }, SPIDERMAN_REDROP_DELAY_MS);
    }
}

/**
 * Mounts the animation into the persistent #spiderman-lottie header-bar
 * element and starts the drop -> idle -> retract state machine. Called
 * once per session from showChatPage(). Mount-once, not "tear down and
 * rebuild on every call" like the old greeting-anchored version - the
 * spidermanMountStarted guard below makes a second call in the same
 * session (session-expiry-then-relogin without a full reload) a no-op
 * rather than restarting an already-running instance.
 */
function initSpiderman() {
    // Synchronous guard, checked BEFORE the async fetch even starts - see
    // the spidermanMountStarted declaration above for why this is a
    // separate check from the generation counter below.
    if (spidermanMountStarted) return;
    spidermanMountStarted = true;
    const myGeneration = spidermanGeneration;

    const container = document.getElementById("spiderman-lottie");
    const input = document.getElementById("chat-input");
    if (!container || !input || typeof lottie === "undefined") {
        spidermanMountStarted = false;   // never actually started - allow a real retry later
        return;
    }

    loadLottieData(SPIDERMAN_LOTTIE_URL).then(data => {
        // A teardown may have run while this fetch was in flight - bail if
        // stale (container.isConnected as a second, independent check).
        if (!data || myGeneration !== spidermanGeneration || !container.isConnected) return;

        // The raw file needs three things filtered before mounting, all
        // found by direct measurement: a static white background plate (a
        // "solid" layer, ty:1), and a full duplicate of the character
        // stacked as [outline, matte, image]. The two image assets
        // ("vmN1QaQglt" real/animated, "6FjzSH3h6q" duplicate) are
        // byte-identical (MD5-confirmed) - the duplicate sits permanently
        // fixed at [11,-21] the whole animation. Removing just the
        // duplicate image + its matte (the layer immediately above it,
        // td:1) still left a third layer one position further back: a
        // black-stroked outline with no refId, also fixed at [11,-21] -
        // rendered as a disconnected outline hovering above the real
        // character. All three must go; the real character's own paired
        // outline stays.
        //
        // Filtered here rather than hidden in the rendered SVG after, so
        // it can't break if lottie-web's DOM structure ever changes. The
        // cache holds the original fetched data (shared across anyone
        // loading this URL), so build a filtered copy instead of mutating
        // it in place.
        const STUCK_DUPLICATE_REFID = "6FjzSH3h6q";
        const rawLayers = data.layers || [];
        const duplicateIdx = rawLayers.findIndex(l => l.refId === STUCK_DUPLICATE_REFID);
        // Track mattes aren't referenced by ID - the source is always the
        // layer immediately above its consumer, marked td:1.
        const duplicateMatteIdx = (duplicateIdx > 0 && rawLayers[duplicateIdx - 1].td === 1)
            ? duplicateIdx - 1 : -1;
        // The outline sits one more position back - not a matte (no
        // td/tt), just a third layer with the same constant [11,-21]
        // position signature as the duplicate image.
        const duplicateOutlineIdx = (duplicateMatteIdx > 0) ? duplicateMatteIdx - 1 : -1;
        const filteredData = {
            ...data,
            layers: rawLayers.filter((l, i) =>
                l.ty !== 1 && i !== duplicateIdx && i !== duplicateMatteIdx && i !== duplicateOutlineIdx)
        };

        spidermanAnim = lottie.loadAnimation({
            container,
            renderer: "svg",
            loop: false,
            autoplay: false,
            animationData: filteredData,
            rendererSettings: { preserveAspectRatio: "xMidYMid meet" }
        });

        spidermanAnim.addEventListener("DOMLoaded", () => {
            // Same staleness guard as above - a teardown can still land in
            // the (very short) window between lottie.loadAnimation() and
            // this event firing.
            if (myGeneration !== spidermanGeneration) return;
            const svg = container.querySelector("svg");
            if (!svg) return;
            svg.setAttribute("viewBox", SPIDERMAN_VIEWBOX_CROP);
            // lottie-web's SVG renderer also applies its own clip-path,
            // sized to the native 0,0-32,32 canvas, independent of the
            // viewBox above - the character sits at y:-21 (above y:0), so
            // it was clipped invisible before the viewBox crop could even
            // show it (confirmed: getBoundingClientRect() at frame 32 came
            // back all zeros). Widen the clipPath rect to match
            // SPIDERMAN_VIEWBOX_CROP rather than removing it, so real
            // out-of-bounds content still clips correctly.
            const clipRect = svg.querySelector("clipPath rect");
            if (clipRect) {
                clipRect.setAttribute("x", "8");
                clipRect.setAttribute("y", "-23");
                clipRect.setAttribute("width", "16");
                clipRect.setAttribute("height", "46");
            }
            dropSpiderman();
        });

        spidermanInputListener = () => retractSpiderman(false);
        spidermanInputTarget = input;
        input.addEventListener("input", spidermanInputListener);
    });
}

function loadLottieData(url) {
    if (_lottieDataCache[url]) return Promise.resolve(_lottieDataCache[url]);
    if (_lottieLoadingCache[url]) return _lottieLoadingCache[url];

    _lottieLoadingCache[url] = fetch(url)
        .then(res => res.json())
        .then(data => {
            _lottieDataCache[url] = data;
            return data;
        })
        .catch(() => null);   // animation is decorative - never break the UI over it

    return _lottieLoadingCache[url];
}

/**
 * Mount a Lottie animation into a container element. Defaults to the bot
 * mascot; pass CHAMELEON_LOTTIE_URL (or any other URL) for a different one.
 * Safe to call before the JSON has downloaded - it waits, then renders.
 */
function mountBotLottie(container, url = BOT_LOTTIE_URL) {
    if (!container || typeof lottie === "undefined") return;

    loadLottieData(url).then(data => {
        if (!data || !container.isConnected) return;
        const anim = lottie.loadAnimation({
            container: container,
            renderer: "svg",
            loop: true,
            autoplay: true,
            animationData: data,
            rendererSettings: {
                // Container and viewBox are both square here, so this makes
                // no visual difference on its own - the real fix for "tiny
                // character, excess padding" is the viewBox crop below.
                // Harmless to set, correct default for any future non-square case.
                preserveAspectRatio: "xMidYMid slice"
            }
        });

        // See LOTTIE_VIEWBOX_CROPS above. lottie-web has no loadAnimation()
        // option for a custom crop rectangle, so this just overwrites the
        // viewBox attribute it already set (normally "0 0 <w> <h>", the
        // animation's full native canvas) after mounting.
        const crop = LOTTIE_VIEWBOX_CROPS[url];
        if (crop) {
            const svg = container.querySelector("svg");
            if (svg) svg.setAttribute("viewBox", crop);
        }

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

// =========================================================
// MOBILE KEYBOARD LAYOUT FIX
// .full-height falls back to 100dvh, but that alone doesn't reliably
// repaint when the keyboard closes - it only worked when a keystroke also
// triggered a reflow (autoResize()), not when the keyboard was dismissed
// with nothing typed. visualViewport's resize event fires on every
// keyboard transition regardless, so we mirror its height into --vvh and
// let .full-height read that instead.
// =========================================================
function initViewportHeightFix() {
    if (!window.visualViewport) return;   // no visualViewport - 100dvh fallback still applies
    const applyViewportHeight = () => {
        const h = window.visualViewport.height;
        // Can read 0 on first fire before layout settles - "0px" is valid
        // CSS, so writing it would permanently defeat the var(--vvh, 100dvh)
        // fallback and collapse the whole page. Skip bad readings instead.
        if (!(h > 0)) return;
        document.documentElement.style.setProperty("--vvh", `${h}px`);

        // iOS's native "scroll focused input into view" can still nudge
        // #chat-messages or the page itself when the keyboard opens, even
        // with --vvh sized correctly - overflow:hidden blocks user
        // drag-scroll but not this programmatic one. Reset both possible
        // scroll owners to their correct resting position on every
        // keyboard transition rather than trying to predict which one moved.
        window.scrollTo(0, 0);
        const container = document.getElementById("chat-messages");
        if (container) {
            container.scrollTop = messageCount === 0 ? 0 : container.scrollHeight;
        }
    };
    window.visualViewport.addEventListener("resize", applyViewportHeight);
    applyViewportHeight();
}

document.addEventListener("DOMContentLoaded", () => {
    initViewportHeightFix();

    document.getElementById("login-password").addEventListener("keydown", (e) => {
        if (e.key === "Enter") handleLogin();
    });
    document.getElementById("login-username").addEventListener("keydown", (e) => {
        if (e.key === "Enter") handleLogin();
    });

    // Login page mascot - the element exists and is visible right away.
    mountBotLottie(document.getElementById("login-lottie"));

    // Independent of login state - GET /api/system-status needs no auth,
    // and the disabled banner/input/chameleon treatment applies to every
    // role once on the chat page, so this has to run regardless of
    // whether restoreSession() below finds a session at all.
    checkSystemStatus();

    document.getElementById("kill-switch-btn").addEventListener("click", openKillModal);
    document.getElementById("kill-modal-cancel").addEventListener("click", closeKillModal);

    const killHoldBtn = document.getElementById("kill-hold-btn");
    killHoldBtn.addEventListener("mousedown", startKillHold);
    killHoldBtn.addEventListener("mouseup", cancelKillHold);
    killHoldBtn.addEventListener("mouseleave", cancelKillHold);
    killHoldBtn.addEventListener("touchstart", (e) => { e.preventDefault(); startKillHold(); });
    killHoldBtn.addEventListener("touchend", cancelKillHold);
    killHoldBtn.addEventListener("touchcancel", cancelKillHold);

    document.getElementById("notifications-btn").addEventListener("click", handleNotificationsClick);

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


/**
 * Renders the greeting block's text content (time/name/sub). Called both
 * from showChatPage() (first render) and clearChat() (the greeting
 * reappears after a wipe) so the two never drift out of sync.
 *
 * No longer builds a Spider-Man anchor here - he moved to the persistent
 * header bar (see app.js's SPIDER-MAN EASTER EGG section and
 * #spiderman-lottie in index.html), a static element outside the greeting
 * that clearChat() never touches, so nothing about his lifecycle depends
 * on this function anymore.
 */
function renderGreeting(profile) {
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
    // Decorative chameleon, perched on the sidebar's top edge - same
    // "mount once" guard as the sidebar mascot above.
    const sidebarChameleon = document.getElementById("sidebar-chameleon");
    if (sidebarChameleon && !sidebarChameleon.hasChildNodes()) {
        mountBotLottie(sidebarChameleon, CHAMELEON_LOTTIE_URL);
    }

    currentProfile = profile;
    renderGreeting(profile);

    // No-op if already mounted this session (see initSpiderman()).
    initSpiderman();

    buildIDCard(profile);
    buildQuickActions();
    initKillSwitch();
    document.getElementById("notifications-btn").classList.remove("hidden");
    checkNotificationsCount();
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

    } else if (userRole === "hod" || userRole === "vice_principal") {
        // hod/vice_principal log in as a teacher record (see app.py's
        // _build_profile()) - same profile shape as the teacher branch
        // above, plus a department name to show instead of just "Teacher".
        const roleLabel = userRole === "hod" ? "HOD" : "Vice Principal";
        nameEl.textContent = profile.name;
        subEl.textContent  = profile.department ? `${roleLabel} — ${profile.department}` : roleLabel;
        statsEl.innerHTML  = `
            <div class="flex-1 bg-white bg-opacity-20 rounded-xl p-2 text-center">
                <div class="text-base font-bold">🏢</div>
                <div class="text-xs opacity-75 uppercase tracking-wide">${roleLabel}</div>
            </div>
        `;
        document.querySelector("#id-card .text-xs").textContent = roleLabel;

    } else {
        // principal, assistant_principal - app.py's _build_profile()
        // already returns the correct label ("Principal"/"Assistant
        // Principal") as profile.name, so no role check needed here.
        nameEl.textContent = profile.name;
        subEl.textContent  = "Administration";
        statsEl.innerHTML  = `
            <div class="flex-1 bg-white bg-opacity-20 rounded-xl p-2 text-center">
                <div class="text-base font-bold">🏫</div>
                <div class="text-xs opacity-75 uppercase tracking-wide">Admin</div>
            </div>
        `;
        document.querySelector("#id-card .text-xs").textContent = profile.name;
    }
}

function buildQuickActions() {
    const container = document.getElementById("quick-actions");
    const actions = quickActions[userRole] || [];

    // Tailwind's CDN/Play JIT compiler only reliably generates CSS for a
    // class if it also appears somewhere in the static HTML - a class used
    // ONLY in this JS template string generated no CSS at all (these
    // buttons rendered with zero padding despite the class being in the
    // DOM). Fixed by using px-4/py-3, already used elsewhere in the static
    // HTML. Keep this in mind for any new class added only in JS-built markup.
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
// PRINCIPAL-ONLY KILL SWITCH
// chatbotEnabled is checked on every page load (checkSystemStatus(), no
// auth needed - GET /api/system-status) and updated immediately after a
// successful kill, so the disabled state never depends on a page reload.
// The hold-to-disable ring is driven with direct inline style writes, not
// a CSS class, since an early release has to snap it back INSTANTLY - see
// the .kill-hold-ring-progress comment in index.html for why that rules
// out a single class-swap transition.
// =========================================================
let chatbotEnabled = true;
const KILL_RING_CIRCUMFERENCE = 282.74; // 2*pi*45, matches the SVG's r=45
const KILL_HOLD_MS = 5000;
let killHoldTimer = null;

async function checkSystemStatus() {
    try {
        const res = await fetch("/api/system-status");
        const data = await res.json();
        chatbotEnabled = data.enabled !== false;
    } catch (e) {
        chatbotEnabled = true; // unreachable - fail open, same default as app.py's _chatbot_enabled()
    }
    applySystemStatus();
}

function applySystemStatus() {
    const input = document.getElementById("chat-input");
    const inputBox = document.getElementById("chat-input-box");
    const banner = document.getElementById("disabled-banner");
    const chameleon = document.getElementById("sidebar-chameleon");
    const killBtn = document.getElementById("kill-switch-btn");
    if (!input) return; // login page - nothing to apply yet

    input.disabled = !chatbotEnabled;
    inputBox.classList.toggle("chat-disabled", !chatbotEnabled);
    banner.classList.toggle("hidden", chatbotEnabled);
    chameleon.classList.toggle("chameleon-disabled", !chatbotEnabled);
    killBtn.classList.toggle("kill-switch-btn-off", !chatbotEnabled);
}

// Only the principal (literally - not assistant_principal, see app.py's
// /api/kill-switch docstring) ever sees this button at all.
function initKillSwitch() {
    document.getElementById("kill-switch-btn").classList.toggle("hidden", userRole !== "principal");
}


// =========================================================
// NOTIFICATIONS BADGE - visual only (see the HTML comment by
// #notifications-btn and app.py's /api/notices-count/-seen). Not tied to
// the NLP "notices" intent internally - it's a separate, simpler signal
// that only needs a count, checked on login and refreshed after the badge
// is engaged.
// =========================================================
async function checkNotificationsCount() {
    try {
        const res = await fetch("/api/notices-count");
        const data = await res.json();
        const badge = document.getElementById("notifications-badge-count");
        if (data.count > 0) {
            badge.textContent = data.count > 9 ? "9+" : String(data.count);
            badge.classList.remove("hidden");
        } else {
            badge.classList.add("hidden");
        }
    } catch (e) {
        // Unreachable - leave whatever badge state was already showing
        // rather than guessing; the next successful check corrects it.
    }
}

// Marks everything seen AND asks the chatbot for the actual notices,
// reusing the existing quick-action send path (sendQuick()) instead of
// building a separate notices panel UI just for this button.
async function handleNotificationsClick() {
    try {
        await fetch("/api/notices-seen", { method: "POST" });
    } catch (e) {
        // Best-effort - still ask for the notices below even if marking
        // seen failed, the count will just stay stale until the next check.
    }
    checkNotificationsCount();
    sendQuick("any new announcements");
}

function openKillModal() {
    if (!chatbotEnabled) return; // already off - button is inert, but belt and suspenders
    document.getElementById("kill-modal-warning").classList.remove("hidden");
    document.getElementById("kill-modal-hold-wrap").classList.remove("hidden");
    document.getElementById("kill-modal-success").classList.add("hidden");
    document.getElementById("kill-modal-cancel").classList.remove("hidden");
    resetKillRing();
    document.getElementById("kill-modal-backdrop").classList.remove("hidden");
}

function closeKillModal() {
    document.getElementById("kill-modal-backdrop").classList.add("hidden");
    cancelKillHold();
}

function resetKillRing() {
    const ring = document.getElementById("kill-hold-ring-progress");
    ring.style.transition = "none";
    ring.style.strokeDashoffset = String(KILL_RING_CIRCUMFERENCE);
    void ring.getBoundingClientRect(); // force reflow so the next transition doesn't merge with this reset
    ring.style.transition = "";
}

function startKillHold() {
    if (killHoldTimer) return; // already holding (e.g. a duplicate mousedown+touchstart)
    const ring = document.getElementById("kill-hold-ring-progress");
    resetKillRing();
    ring.style.transition = `stroke-dashoffset ${KILL_HOLD_MS}ms linear`;
    ring.style.strokeDashoffset = "0";
    killHoldTimer = setTimeout(fireKillSwitch, KILL_HOLD_MS);
}

// Released early: reset the timer AND snap the ring back immediately -
// "if they release early, the progress resets and nothing happens".
function cancelKillHold() {
    if (!killHoldTimer) return;
    clearTimeout(killHoldTimer);
    killHoldTimer = null;
    resetKillRing();
}

async function fireKillSwitch() {
    killHoldTimer = null;
    try {
        const res = await fetch("/api/kill-switch", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ action: "disable" })
        });
        const data = await res.json();
        if (res.ok && data.success) {
            showKillSuccess();
        } else {
            cancelKillHold();
        }
    } catch (e) {
        cancelKillHold();
    }
}

function showKillSuccess() {
    document.getElementById("kill-modal-warning").classList.add("hidden");
    document.getElementById("kill-modal-hold-wrap").classList.add("hidden");
    document.getElementById("kill-modal-cancel").classList.add("hidden");
    document.getElementById("kill-modal-success").classList.remove("hidden");

    chatbotEnabled = false;
    applySystemStatus();

    setTimeout(closeKillModal, 2000);
}


// =========================================================
// SESSION EXPIRY
// /api/chat checks the Flask session before picking a lane, so a 401
// always comes back as plain JSON, never mid-SSE-stream - one check right
// after the fetch resolves covers both lanes. Previously a 401 fell
// through to the normal reply-rendering path and showed the raw
// {"error":"Not logged in."} JSON in a chat bubble, leaving the user stuck
// typing into a chat that could never work again without a refresh.
// =========================================================
function handleSessionExpired() {
    if (sessionExpiredHandled) return;
    sessionExpiredHandled = true;

    removeTypingBubble();
    clearChat();
    // This IS the real Spider-Man teardown boundary now - #chat-page gets
    // hidden right below, so without this his idle/re-drop timers would
    // keep firing indefinitely against a header bar nobody can see.
    teardownSpiderman();
    userRole = null;
    currentProfile = null;

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
    if (input.disabled) return; // kill-switched - input already reflects this, nothing to send
    const message = input.value.trim();
    if (!message) return;

    input.value = "";
    autoResize(input);

    if (messageCount === 0) {
        document.getElementById("greeting").style.display = "none";
        // Spider-Man deliberately isn't torn down here - he lives in the
        // persistent main chat area (#spiderman-lottie), not nested inside
        // the greeting, and keeps running for the whole session. See the
        // SPIDER-MAN EASTER EGG section above for the real teardown boundary.
    }
    messageCount++;

    appendMessage("user", message);
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
            // A stale tab that hasn't re-checked /api/system-status yet
            // can still send one message through before catching up - if
            // the backend says disabled, lock the UI immediately rather
            // than waiting for the next page load.
            if (data.disabled) {
                chatbotEnabled = false;
                applySystemStatus();
            }
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

    // No Spider-Man teardown here anymore - #spiderman-lottie lives outside
    // #chat-messages (a sibling in the main chat area, not inside it), so
    // this innerHTML replacement never touches it, and he's meant to keep
    // running undisturbed through a "Clear Chat" click.
    const container = document.getElementById("chat-messages");
    container.innerHTML = `
        <div id="greeting" class="flex flex-col items-center justify-center h-full text-center pb-20">
            <div id="greeting-time" class="text-xs font-semibold text-gray-600 uppercase tracking-wider mb-2"></div>
            <h2 id="greeting-name" class="font-playfair text-4xl font-bold text-gray-900 tracking-tight mb-2"></h2>
            <p id="greeting-sub" class="text-base text-gray-500"></p>
        </div>
    `;
    // Wiping innerHTML detaches every message avatar, so destroy their
    // animations rather than leaving them running in the background.
    cleanupDetachedLottie();

    // renderGreeting() re-sets name+sub too, not just time - a previous
    // version only re-set greeting-time, leaving name/sub blank after
    // every "Clear Chat" click.
    if (currentProfile) {
        renderGreeting(currentProfile);
    } else {
        // Defensive fallback - clearChat() should only ever run after
        // showChatPage() has already set currentProfile, but degrade to
        // the old minimal reset rather than leaving greeting-time blank
        // too if that assumption is ever wrong.
        const hour = new Date().getHours();
        document.getElementById("greeting-time").textContent =
            hour < 12 ? "Good morning" : hour < 17 ? "Good afternoon" : "Good evening";
    }
}


// =========================================================
// LOGOUT
// =========================================================
async function handleLogout() {
    // The full page reload below wipes everything regardless, but tear
    // down explicitly first anyway - the fetch is a real network round
    // trip, and this closes the (small) window where the timers could
    // otherwise keep ticking if the reload were ever delayed or interrupted.
    teardownSpiderman();
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
