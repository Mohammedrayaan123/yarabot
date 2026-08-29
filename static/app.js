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
// Anchored to the persistent main chat area (#spiderman-lottie in
// index.html, a direct child of the flex-1 main-content wrapper, NOT the
// sidebar - centered over the chat content itself, roughly above the
// greeting, same spot he occupied before this move). Mounts ONCE per
// session, in showChatPage(), and stays active for the whole session -
// including after messages are sent, unlike the old greeting-anchored
// version, which tore down the instant the greeting was hidden. Only torn
// down by an actual session boundary: handleSessionExpired() or
// handleLogout(), never by sendMessage() or clearChat() anymore. See
// initSpiderman()/teardownSpiderman() below for the full lifecycle.
// =========================================================
const SPIDERMAN_LOTTIE_URL = "/static/animation_spider.json";

// Content bounds - CORRECTED. The previous crop ("0 -21 32 53") used the
// full native 0-32 canvas WIDTH, inherited from an early getBBox() sweep
// that (as later analysis in this file established) wasn't a reliable way
// to measure this particular file. Real bug this caused: it left the
// character rendering at roughly 30% of the container's width - a visibly
// tiny, easy-to-miss speck padded by empty crop space on both sides, not
// an actually-invisible-to-the-DOM bug (mount/opacity/z-index were all
// fine) but invisible in practice.
//
// Re-measured properly this time - read the exact SVG transform matrix
// (matrix(scale,0,0,scale,x,y)) on the character's own <g> wrapper across
// every frame, not just a bounding-box sweep. The scale (0.030215827748179)
// and X position (10.923741340637207) are IDENTICAL at every single frame
// - he never moves horizontally, only vertically - so with the native
// asset at 336x695px, his real on-canvas footprint is:
//   width:  336 * 0.030215827748179 ≈ 10.15   (x: 10.92 to 21.08)
//   height: 695 * 0.030215827748179 ≈ 21.00   (y: -21 to 21 across all
//                                               frames' y-translate values)
// "8 -23 16 46" below crops tightly to that (a ~2-unit margin on every
// side), instead of the old crop's ~11-unit empty margin on each side
// horizontally. --spiderman-perch-height-* in index.html was recomputed to
// match this crop's real 16:46 aspect ratio too - the old height values
// were sized for the wrong (32:53) aspect ratio, which let "meet" scaling
// add yet more letterboxing on top of the crop's own dead space.
const SPIDERMAN_VIEWBOX_CROP = "8 -23 16 46";

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
// True from the moment initSpiderman() starts until teardownSpiderman()
// runs - guards against a real race: loadLottieData() is async, so if
// showChatPage() somehow ran twice in one session (e.g. a session-expiry-
// then-relogin without a full page reload) before the first mount's fetch
// resolves, a second initSpiderman() call would start a SECOND concurrent
// mount. Checked synchronously at the top of initSpiderman(), before the
// fetch even starts - spidermanGeneration below only protects the async
// callback itself, not this earlier window.
let spidermanMountStarted = false;
// Bumped by both initSpiderman() and teardownSpiderman() - guards against a
// second real race: if a genuine teardown (handleSessionExpired() /
// handleLogout()) runs WHILE the fetch from an in-flight initSpiderman()
// is still pending, teardownSpiderman() runs while spidermanAnim is still
// null (nothing to tear down yet) - a plain no-op. Without this counter,
// the mount+drop that finishes moments later would go ahead anyway,
// leaving a live, ticking instance running after the session has actually
// ended. Each async callback captures the generation at call time and
// bails if it no longer matches - confirmed by a rapid-fire clearChat()
// stress test that reproduced exactly this without the check, back when
// clearChat() was still a teardown trigger (it no longer is - see
// clearChat() itself).
let spidermanGeneration = 0;
// The logged-in user's profile, kept around so clearChat() can rebuild the
// greeting (name/sub) after wiping the DOM, the same way showChatPage()
// builds it the first time.
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
 * destroys the Lottie instance. The real teardown boundary now is an
 * actual session end - handleSessionExpired() or handleLogout() - since
 * #spiderman-lottie is a persistent element in the header bar that
 * survives sendMessage() and clearChat() entirely (neither touches it
 * anymore). Resetting spidermanMountStarted here (not just spidermanAnim)
 * is what allows a legitimate FRESH mount later, if the user logs back in
 * again in the same page load (session-expiry-then-relogin) rather than
 * via a full page reload.
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
    // The container persists in the DOM across the whole session (it's
    // not rebuilt like the old greeting-nested one was), so a genuine
    // teardown must also reset its visual state directly - otherwise a
    // later fresh mount could start from a stale "dropped" class left over
    // from before this teardown ran.
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
        // A genuine teardown (handleSessionExpired()/handleLogout()) may
        // have run while this fetch was in flight, bumping
        // spidermanGeneration - a mismatch here means this mount is stale
        // and must NOT proceed (that's what would otherwise leave a live
        // instance running after the session already ended).
        // container.isConnected is kept too as a second, independent guard
        // for the same case.
        if (!data || myGeneration !== spidermanGeneration || !container.isConnected) return;

        // The raw file has THREE things needing filtering before mounting,
        // all found by direct measurement (never assumed) - the source
        // file has TWO complete copies of the character stacked as
        // [outline shape, matte shape, image] groups, and only one whole
        // group should ever be visible:
        //
        // 1. A static, always-visible white background plate (a Lottie
        //    "solid" layer, ty:1) covering the full native canvas - fine as
        //    a standalone sticker, wrong for an overlay decoration.
        //
        // 2 & 3. A genuine DUPLICATE of the character. The file has TWO
        //    image assets ("vmN1QaQglt" and "6FjzSH3h6q") that are
        //    byte-identical (confirmed via MD5) - the same artwork twice.
        //    refId "vmN1QaQglt" is the real, animated character (position
        //    keyframes actually change frame to frame). refId
        //    "6FjzSH3h6q" is a second copy permanently fixed at the
        //    settled position [11,-21] the WHOLE animation - this was the
        //    "second Spider-Man, stuck, never animates" bug fixed earlier.
        //    But that fix only removed the image + its OWN matte (the
        //    layer immediately before it, td:1) - it missed a THIRD layer
        //    in the same duplicate group: a black-stroked OUTLINE shape,
        //    one more position back, that has no refId to match on (only
        //    image layers reference assets) and is ALSO permanently fixed
        //    at [11,-21] confirmed the exact same way (every keyframe
        //    holds an identical value). Left in, it rendered as a
        //    disconnected black outline hovering above the real character
        //    with visible empty space between them - not a separate "web"
        //    at all, just the other half of the same duplicate-content bug.
        //    The real character's OWN matching outline (the layer paired
        //    with the kept image) stays - only the duplicate's group of
        //    three is removed entirely.
        //
        // Filtering the data before mounting (rather than hiding elements
        // in the rendered SVG afterwards) is the more robust fix - it
        // can't come back if a future lottie-web version changes how it
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
        // The duplicate's outline shape sits one MORE position back - it's
        // not a matte (no td/tt at all), just a third standalone layer that
        // belongs to the same duplicate group. Confirmed on this file:
        // index (duplicateMatteIdx - 1) has a CONSTANT position identical
        // to the duplicate's own [11,-21], the same "every keyframe holds
        // the same value" signature used to identify the duplicate image
        // itself in the first place.
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

// =========================================================
// MOBILE KEYBOARD LAYOUT FIX
// .full-height (index.html) uses 100dvh so the page shrinks around the
// on-screen keyboard instead of scrolling the header off-screen - see the
// CSS comment there for the original bug. That fix alone left a real gap:
// it only reliably re-applied when SOMETHING ELSE also forced a layout
// reflow around the same time (e.g. autoResize() mutating the textarea's
// own height on every keystroke) - dismissing the keyboard with no text
// ever typed had no such trigger, so on some mobile browsers the page
// stayed stuck in the shrunk "keyboard open" layout even after the
// keyboard was gone. `100dvh` recalculating correctly is not guaranteed to
// also repaint on its own the instant the keyboard closes on every engine.
//
// visualViewport's own 'resize' event exists specifically for this - it
// fires on ANY on-screen-keyboard open/close, independent of typing or any
// other DOM event, which is exactly the trigger the old CSS-only fix was
// missing. Explicitly writing the live visual viewport height to a CSS
// custom property on every fire, and having .full-height fall back to it,
// guarantees the layout is forced to match reality on every keyboard
// transition, not just the ones that happen to coincide with a keystroke.
function initViewportHeightFix() {
    if (!window.visualViewport) return;   // older browsers - the 100dvh fallback still applies
    const applyViewportHeight = () => {
        const h = window.visualViewport.height;
        // Real bug found live: visualViewport.height can genuinely read 0
        // (or any other bogus non-positive value) at the moment this first
        // runs, very early in page/tab setup before layout has settled -
        // and since "0px" is a syntactically VALID CSS length, writing it
        // to --vvh permanently defeats .full-height's own `var(--vvh,
        // 100dvh)` fallback (fallbacks only kick in when the custom
        // property is UNSET, not when it holds a valid-but-wrong value).
        // That collapsed the entire #chat-page to zero height - not a
        // Spider-Man-specific bug, the WHOLE app appeared broken. If no
        // later 'resize' event happens to fire to correct it, --vvh stays
        // stuck at 0 for the rest of the session. Guarding here means a
        // bad reading is simply skipped - the 100dvh fallback (or whatever
        // --vvh already held from a previous good reading) stays in
        // effect instead of being overwritten with garbage.
        if (!(h > 0)) return;
        document.documentElement.style.setProperty("--vvh", `${h}px`);

        // Real bug found live (real device, not reproducible through this
        // tool's viewport emulation - CDP-level resize genuinely shrinks
        // the layout viewport, so #chat-messages already fits with zero
        // scroll room in every local test; a real on-screen keyboard only
        // shrinks the VISUAL viewport unless the browser both supports and
        // honors interactive-widget=resizes-content, so this has to cover
        // the case where it doesn't): even with --vvh sized correctly, the
        // browser's own native "scroll focused input into view" can still
        // nudge either #chat-messages or the page itself down the instant
        // the keyboard opens - body has overflow:hidden, which blocks the
        // user's own drag-scroll gestures but NOT this programmatic reveal
        // scroll on iOS - leaving the greeting/Spider-Man scrolled out of
        // view above a dead gap, with nothing ever resetting it back.
        // Resetting both possible scroll owners to their correct resting
        // position - top if the greeting is still showing (no messages
        // sent yet), otherwise the bottom, matching every other
        // scroll-to-latest call in this file - on every keyboard
        // transition overrides whichever one the browser nudged, instead
        // of needing to know in advance which it was.
        window.scrollTo(0, 0);
        const container = document.getElementById("chat-messages");
        if (container) {
            container.scrollTop = messageCount === 0 ? 0 : container.scrollHeight;
        }
    };
    window.visualViewport.addEventListener("resize", applyViewportHeight);
    applyViewportHeight();
}

// Allow Enter key to submit login
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

    // Set greeting text
    currentProfile = profile;
    renderGreeting(profile);

    // Mount the Spider-Man easter egg in the persistent header bar -
    // once per session; a no-op if already mounted (see initSpiderman()).
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
    const message = input.value.trim();
    if (!message) return;

    input.value = "";
    autoResize(input);

    // Hide greeting on first message
    if (messageCount === 0) {
        document.getElementById("greeting").style.display = "none";
        // Spider-Man deliberately does NOT get torn down here anymore - he
        // lives in the persistent main chat area now (#spiderman-lottie),
        // not nested inside the greeting, and is meant to keep running for
        // the whole session, including after the first message is sent.
        // See the SPIDER-MAN EASTER EGG section above for the actual
        // teardown boundary (session expiry/logout).
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

    // No Spider-Man teardown here anymore - #spiderman-lottie lives outside
    // #chat-messages (a sibling in the main chat area, not inside it), so
    // this innerHTML replacement never touches it, and he's meant to keep
    // running undisturbed through a "Clear Chat" click.
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
    // leaving greeting-name/greeting-sub blank after every "Clear Chat"
    // click - a real bug, unrelated to Spider-Man: renderGreeting() does
    // the full job (name + sub), the same call showChatPage() makes on
    // first login, so the two can't drift apart again.
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
