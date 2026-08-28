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
// Decorative chameleon mascot (sidebar + login card, perched on the top
// edge). Inspected before wiring in: contains a leftover embedded
// "nwsys.png" image layer and a "by <creator>" text credit layer from its
// original source, but NEITHER ever renders in ANY frame of the loop -
// confirmed by stepping through all 140 frames in lottie-web itself. The
// image layer's ip/op (in/out point) are equal, i.e. zero duration, so
// lottie-web keeps its group `display:none` for the entire animation. The
// text layer's actual string content is empty (""), the only text layer
// anywhere in the file including nested comps, so it draws nothing
// regardless of its keyframed opacity. No stripping needed - verified
// empirically, not just by reading the JSON.
const CHAMELEON_LOTTIE_URL = "/static/Camaleon.json";
const _lottieDataCache = {};      // url -> cached parsed JSON
const _lottieLoadingCache = {};   // url -> in-flight fetch, so each url is only requested once
let lottieInstances = [];         // kept so we can destroy them on clearChat()
let lottieIdCounter = 0;

// chatbot.json's character only occupies a fraction of its native 500x500
// canvas - measured empirically (every frame of the 150-frame idle loop,
// via each shape's rendered bounding box mapped back into viewBox units):
// the character's own shapes span roughly x:169-320, y:94-452 throughout
// the whole loop, i.e. only ~30% of the canvas width and ~72% of its
// height, leaving a lot of dead space that made the mascot look tiny
// inside its box. Cropping the rendered SVG's viewBox to a square centered
// on the character - sized to fully contain that whole measured motion
// envelope plus a margin, so the idle bounce never gets clipped - fixes
// that without needing a bigger container. Deliberately does NOT apply to
// Camaleon.json: measured the same way, its "envelope" actually extends
// PAST its own 1080x1080 canvas (a small fly + leaf accent are legitimately
// off-canvas by design) - it doesn't have a padding problem, and cropping
// it the same way would clip real artwork instead of empty space.
const LOTTIE_VIEWBOX_CROPS = {
    [BOT_LOTTIE_URL]: "45 73 400 400",
};

// =========================================================
// SPIDER-MAN EASTER EGG
// Anchored to the wave emoji in the greeting name line ("[Name] 👋"),
// which only exists on the pre-first-message greeting screen. See
// initSpiderman()/teardownSpiderman() below for the full lifecycle.
// =========================================================
const SPIDERMAN_LOTTIE_URL = "/static/animation_spider.json";

// Content bounds measured by rendering the file in lottie-web and reading
// lottie.getRegisteredAnimations()[0].renderer.elements[i].finalTransform
// at sampled frames (same technique as the chatbot.json crop fix - not
// guessed): x spans 0-32, y spans -21 to 32 of the native 32x32 canvas.
// The default "0 0 32 32" viewBox would clip everything above y=0, which
// is where the character sits for almost the entire animation (he settles
// at y=-21) - this crop is required for him to render at all, not cosmetic.
const SPIDERMAN_VIEWBOX_CROP = "0 -21 32 53";

// Frame ranges - also measured directly from the rendered animation, not
// assumed from the filename/description:
//   0-6    fade in (opacity 0 -> 1), near the top
//   6-24   a small hang/settle wobble (y: -9 -> 0 -> -5 -> 0)
//   24-32  the actual drop to the lowest point (y: 0 -> -21)
//   32-62.33 (the rest of the file) - a DEAD HOLD. Every layer's position
//   and opacity is byte-identical at frames 32, 40, 50, and 62 - confirmed
//   both from the raw keyframe values (consecutive keyframes sharing the
//   exact same value) and from live-rendered transform data. There is no
//   baked-in "spring back up" sequence anywhere in this file.
// Because of that, "retract" is implemented as the drop segment played in
// REVERSE (lottie-web supports this natively: playSegments([end, start],
// true) plays backwards) - the only way to get a retraction out of a file
// that only ever animates downward.
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
// Bumped by both initSpiderman() and teardownSpiderman() - guards against a
// real race: loadLottieData() is async, so if the greeting gets hidden
// (first message sent) or rebuilt (clearChat()) BEFORE that fetch
// resolves, teardownSpiderman() runs while spidermanAnim is still null
// (nothing to tear down yet) - a plain no-op. Without this counter, the
// mount+drop that finishes moments later would go ahead anyway, leaving a
// live, ticking instance (and idle timer) behind a hidden/replaced
// greeting. Each async callback captures the generation at call time and
// bails if it no longer matches - confirmed by a rapid-fire clearChat()
// stress test that reproduced exactly this without the check.
let spidermanGeneration = 0;
// The logged-in user's profile, kept around so clearChat() can rebuild the
// greeting (name/sub + the Spider-Man anchor) after wiping the DOM, the
// same way showChatPage() builds it the first time.
let currentProfile = null;

// Explicit state, checked at the top of every trigger (page load, keystroke,
// idle timeout, re-drop check) before it's allowed to act. Without this,
// retractSpiderman() had no way to tell "already retracted" from "currently
// hanging" and called playSegments() unconditionally on every single 'input'
// event - with forceFlag:true that snaps the animation back to its segment's
// START frame before playing, so every keystroke after the first replayed
// the whole retract animation instead of being a no-op. Real bug, found via
// live typing, not just a theoretical concern.
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
 * Full teardown: cancels pending timers, detaches the input listener, and
 * destroys the Lottie instance. MUST run before the greeting is hidden
 * (first message sent) or rebuilt (clearChat()) - otherwise a stale timer
 * fires later and tries to animate/query an element that's gone, or a
 * second init stacks a duplicate 'input' listener and a duplicate idle
 * timer on top of the old one.
 */
function teardownSpiderman() {
    spidermanGeneration++;   // invalidates any in-flight initSpiderman() mount
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
    // The Lottie file's OWN keyframed motion only ever moves the character
    // ~25px on screen (measured directly) - nowhere close to spanning from
    // the emoji down to the subtitle line. The real travel distance is this
    // CSS class toggle (see .spiderman-perch.spiderman-dropped in
    // index.html); playSegments() below just layers the character's own
    // small settle motion on top of it.
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
    // Any retraction cancels whatever's pending - including a re-drop
    // queued by an EARLIER idle timeout, so typing during that 5s window
    // correctly cancels the re-drop (point 4 of the spec). Safe to run even
    // when already retracted (both are no-ops if nothing's armed).
    if (spidermanIdleTimer) { clearTimeout(spidermanIdleTimer); spidermanIdleTimer = null; }
    if (spidermanRedropTimer) { clearTimeout(spidermanRedropTimer); spidermanRedropTimer = null; }

    // The actual animation call only fires on the state TRANSITION into
    // "retracted" - every keystroke after the first correctly becomes a
    // no-op here instead of replaying the retract animation from the top.
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
 * Mounts the animation into #spiderman-lottie (built fresh by
 * renderGreeting()) and starts the drop -> idle -> retract state machine.
 * Safe to call repeatedly - always tears down any previous instance first,
 * so a second call (e.g. from clearChat() re-rendering the greeting) can't
 * leave two Lottie instances or two sets of timers running at once.
 */
function initSpiderman() {
    teardownSpiderman();
    const myGeneration = spidermanGeneration;   // teardownSpiderman() just bumped it

    const container = document.getElementById("spiderman-lottie");
    const input = document.getElementById("chat-input");
    if (!container || !input || typeof lottie === "undefined") return;

    loadLottieData(SPIDERMAN_LOTTIE_URL).then(data => {
        // The greeting may have been hidden (first message) or rebuilt
        // (clearChat()) while this fetch was in flight - either one bumps
        // spidermanGeneration, so a mismatch here means this mount is
        // stale and must NOT proceed (that's what leaves an orphaned timer
        // running behind a hidden/replaced greeting). container.isConnected
        // is kept too as a second, independent guard for the same case.
        if (!data || myGeneration !== spidermanGeneration || !container.isConnected) return;

        // Two things need filtering out of the raw file before mounting -
        // both found by direct measurement, not assumed:
        //
        // 1. A static, always-visible white background plate (a Lottie
        //    "solid" layer, ty:1) covering the full native canvas - fine as
        //    a standalone sticker, wrong for an overlay decoration on top
        //    of the greeting text.
        //
        // 2. A genuine DUPLICATE of the character. The file has TWO image
        //    assets ("vmN1QaQglt" and "6FjzSH3h6q") that are byte-identical
        //    (confirmed via MD5) - the same artwork twice. Layer refId
        //    "vmN1QaQglt" is the real, animated character (its position
        //    keyframes actually change frame to frame - matched by its
        //    matte layer, the shape immediately before it in the array).
        //    Layer refId "6FjzSH3h6q" is a second copy that's permanently
        //    fixed at the settled position and fades in early (by frame 6)
        //    then never moves or disappears again - this is the "second
        //    Spider-Man, stuck, never animates" bug: it was never a
        //    mounting/duplication bug in this code, it's unfiltered
        //    duplicate content in the source file itself. Removing it (and
        //    its own dedicated matte layer, same adjacency rule) along with
        //    the white background is what actually fixes it.
        //
        // Filtering the data before mounting (rather than hiding elements
        // in the rendered SVG afterwards) is the more robust fix for both -
        // it can't come back if a future lottie-web version changes how it
        // structures the DOM. The cache holds the ORIGINAL fetched data
        // (shared with anyone else who might load this URL), so build a
        // filtered copy instead of mutating it in place.
        const STUCK_DUPLICATE_REFID = "6FjzSH3h6q";
        const rawLayers = data.layers || [];
        const duplicateIdx = rawLayers.findIndex(l => l.refId === STUCK_DUPLICATE_REFID);
        // Lottie track mattes aren't referenced by ID - the matte source is
        // always the layer immediately ABOVE its consumer in the array,
        // marked td:1. Confirmed on this file: index (duplicateIdx - 1) has
        // td:1 and nothing else references it once the image above it is
        // gone, so it must be dropped alongside it or it'd have nothing
        // left consuming it.
        const duplicateMatteIdx = (duplicateIdx > 0 && rawLayers[duplicateIdx - 1].td === 1)
            ? duplicateIdx - 1 : -1;
        const filteredData = {
            ...data,
            layers: rawLayers.filter((l, i) => l.ty !== 1 && i !== duplicateIdx && i !== duplicateMatteIdx)
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
            // lottie-web's SVG renderer ALSO applies its own internal
            // clip-path, sized to the animation's native 0,0-32,32 canvas,
            // independently of the viewBox above - found by checking the
            // actual rendered getBoundingClientRect() of the character
            // image at frame 32, which came back {x:0,y:0,w:0,h:0}
            // (invisible) even with the viewBox already fixed. The
            // character sits at y:-21 there - entirely above y:0 - so it
            // was being clipped away before the viewBox crop ever had a
            // chance to show it. Widening this clipPath's rect to the same
            // bounds as SPIDERMAN_VIEWBOX_CROP (rather than removing
            // clip-path entirely) keeps clipping active for anything
            // genuinely outside the measured content area, while letting
            // the actual artwork through.
            const clipRect = svg.querySelector("clipPath rect");
            if (clipRect) {
                clipRect.setAttribute("x", "0");
                clipRect.setAttribute("y", "-21");
                clipRect.setAttribute("width", "32");
                clipRect.setAttribute("height", "53");
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
                // Container and viewBox are both square for every mascot
                // instance, so meet vs. slice makes no visual difference on
                // its own here - the real fix for the "tiny character,
                // excess padding" complaint is the viewBox crop just below.
                // Set anyway (harmless, and correct default going forward
                // for any non-square case added later).
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


/**
 * Renders the greeting block's text content (time/name/sub) and rebuilds
 * the wave-emoji anchor structure Spider-Man mounts into. Called both from
 * showChatPage() (first render) and clearChat() (the greeting reappears
 * after a wipe) so the two never drift out of sync.
 *
 * Builds the name line via DOM methods rather than innerHTML - profile.name
 * comes from the database, and this sidesteps any need to HTML-escape it
 * for a one-line greeting.
 */
function renderGreeting(profile) {
    const hour = new Date().getHours();
    const timeGreeting = hour < 12 ? "Good morning" : hour < 17 ? "Good afternoon" : "Good evening";
    const firstName = profile.name.split(" ")[0];
    const sub = subGreetings[Math.floor(Math.random() * subGreetings.length)];

    document.getElementById("greeting-time").textContent = timeGreeting;

    const nameEl = document.getElementById("greeting-name");
    nameEl.textContent = "";
    nameEl.appendChild(document.createTextNode(firstName + " "));

    // .spiderman-anchor is the positioning reference for #spiderman-lottie
    // (see the CSS comment in index.html) - it's always present regardless
    // of the randomized sub-greeting text, unlike anchoring to specific
    // subtitle words would be.
    const waveWrap = document.createElement("span");
    waveWrap.className = "spiderman-anchor";

    const waveGlyph = document.createElement("span");
    waveGlyph.className = "wave-glyph";
    waveGlyph.textContent = "👋";
    waveWrap.appendChild(waveGlyph);

    const spidermanContainer = document.createElement("div");
    spidermanContainer.id = "spiderman-lottie";
    spidermanContainer.className = "spiderman-perch";
    spidermanContainer.setAttribute("aria-hidden", "true");
    waveWrap.appendChild(spidermanContainer);

    nameEl.appendChild(waveWrap);

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

    // Set greeting (+ mount the Spider-Man easter egg on the wave emoji)
    currentProfile = profile;
    renderGreeting(profile);
    initSpiderman();

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
    const message = input.value.trim();
    if (!message) return;

    input.value = "";
    autoResize(input);

    // Hide greeting on first message
    if (messageCount === 0) {
        document.getElementById("greeting").style.display = "none";
        // The greeting isn't removed from the DOM here (just hidden via
        // CSS), so cleanupDetachedLottie()'s isConnected check would never
        // catch a stale Spider-Man timer/animation on its own - tear it
        // down explicitly instead, or the 30s idle timer (and a possible
        // 5s re-drop timer after it) keeps firing in the background,
        // animating an element nobody can see, for the rest of the session.
        teardownSpiderman();
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

    // Tear down BEFORE wiping the DOM: the innerHTML replacement below
    // destroys the old #spiderman-lottie element out from under the
    // running animation, and would otherwise leave its idle/re-drop timers
    // dangling with nothing valid left to act on.
    teardownSpiderman();

    const container = document.getElementById("chat-messages");
    // Remove all messages but keep the greeting
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

    // Re-set greeting text. Previously this only re-set greeting-time,
    // leaving greeting-name/greeting-sub (and the wave emoji Spider-Man
    // anchors to) blank after every "Clear Chat" click - a real bug, not
    // just missing Spider-Man wiring: renderGreeting() does the full job
    // (name + sub + rebuilding the wave-emoji anchor), the same call
    // showChatPage() makes on first login, so the two can't drift apart
    // again. initSpiderman() then starts a completely fresh state machine
    // against the newly-built anchor - it already tears down any previous
    // instance itself, so this can't stack duplicate timers on repeated
    // clears.
    if (currentProfile) {
        renderGreeting(currentProfile);
        initSpiderman();
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
