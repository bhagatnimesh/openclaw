(function () {
  var refreshMs = 5 * 60 * 1000;
  var wakeLockCheckMs = 60 * 1000;
  var defaultWakeLockWindow = "06:00-22:00";
  var wakeLockSentinel = null;
  var wakeLockPolicy = parseWakeLockPolicy();
  var keepAliveVideo = null;
  var keepAliveCanvas = null;
  var keepAliveFrameTimer = null;
  var keepAliveFrame = 0;
  var lastTasksData = {};
  var lastReadingData = {};
  var lastShoppingData = {};
  var lastBacklogData = {};
  var lastBedtimeData = {};
  var backlogReviewIndex = -1;
  var selectedReadingChild = "Nysha";
  var selectedShoppingList = "all";
  var bedtimeVoiceEnabled = false;

  function byId(id) {
    return document.getElementById(id);
  }

  function text(value, fallback) {
    if (value === null || value === undefined || value === "") {
      return fallback || "";
    }
    return String(value);
  }

  function escapeHtml(value) {
    return text(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  function titleLabel(value) {
    return text(value)
      .replace(/[-_]+/g, " ")
      .replace(/\b\w/g, function (match) {
        return match.toUpperCase();
      });
  }

  function setText(id, value) {
    var node = byId(id);
    if (node) {
      node.textContent = text(value);
    }
  }

  function setSummaryChip(id, value, singular, plural) {
    var node = byId(id);
    if (!node) return;
    var count = Number(value || 0);
    node.innerHTML =
      "<strong>" + count + "</strong> " + escapeHtml(count === 1 ? singular : plural);
  }

  function classToken(value, fallback) {
    return (
      text(value, fallback)
        .toLowerCase()
        .replace(/[^a-z0-9_-]+/g, "-")
        .replace(/^-+|-+$/g, "") || fallback
    );
  }

  function empty(label) {
    return '<p class="empty">' + escapeHtml(label) + "</p>";
  }

  function setCardHidden(cardKey, hidden) {
    var node = document.querySelector('[data-dashboard-card="' + cardKey + '"]');
    if (node) {
      node.hidden = !!hidden;
    }
  }

  function setContainerHidden(id, hidden) {
    var node = byId(id);
    if (node) {
      node.hidden = !!hidden;
    }
  }

  function closestActionTarget(target) {
    while (target && target !== document) {
      if (
        target.getAttribute("data-task-complete") !== null ||
        target.getAttribute("data-task-tag-filter") !== null ||
        target.getAttribute("data-task-owner-chip") !== null ||
        target.getAttribute("data-reading-child") !== null ||
        target.getAttribute("data-reading-heatmap-day") !== null ||
        target.getAttribute("data-reading-update") !== null ||
        target.getAttribute("data-reading-delete") !== null ||
        target.getAttribute("data-bedtime-ack") !== null ||
        target.getAttribute("data-bedtime-voice") !== null ||
        target.getAttribute("data-decision-complete") !== null ||
        target.getAttribute("data-backlog-action") !== null ||
        target.getAttribute("data-shopping-tab") !== null ||
        target.getAttribute("data-shopping-check") !== null ||
        target.getAttribute("data-shopping-clear") !== null
      ) {
        return target;
      }
      target = target.parentNode;
    }
    return null;
  }

  function currentSectionId() {
    var hash = window.location.hash || "#today";
    if (!/^#[A-Za-z0-9_-]+$/.test(hash)) return "today";
    return hash.slice(1) || "today";
  }

  function updateActiveNav() {
    var activeHref = "#" + currentSectionId();
    Array.prototype.forEach.call(document.querySelectorAll(".portal-nav a"), function (link) {
      link.classList.toggle("is-active", link.getAttribute("href") === activeHref);
    });
  }

  function scrollToHashSection() {
    var id = currentSectionId();
    var section = byId(id);
    if (section) {
      section.scrollIntoView({ block: "start" });
    }
  }

  function getQueryParam(name) {
    try {
      var query = new URLSearchParams(window.location.search);
      return query.has(name) ? query.get(name) : null;
    } catch (_error) {
      return null;
    }
  }

  function normalizeTag(value) {
    return text(value)
      .trim()
      .replace(/^#/, "")
      .toLowerCase()
      .replace(/[^a-z0-9_-]+/g, "-")
      .replace(/^-+|-+$/g, "");
  }

  function selectedTaskTag() {
    return normalizeTag(getQueryParam("tag"));
  }

  function normalizeTaskOwner(value) {
    return text(value).trim().toLowerCase() || "unknown";
  }

  function selectedTaskOwner() {
    var value = getQueryParam("owner");
    if (value === null) return "all";
    return normalizeTaskOwner(value);
  }

  function selectedTaskDueFilter() {
    return text(getQueryParam("due")).trim().toLowerCase() === "today" ? "today" : "";
  }

  function setSelectedTaskTag(tag) {
    var selected = normalizeTag(tag);
    var url = new URL(window.location.href);
    if (selected) {
      url.searchParams.set("tag", selected);
      url.hash = "#tasks";
    } else {
      url.searchParams.delete("tag");
    }
    window.history.replaceState({}, "", url.toString());
  }

  function setSelectedTaskOwner(owner) {
    var selected = normalizeTaskOwner(owner);
    var url = new URL(window.location.href);
    if (selected && selected !== "all") {
      url.searchParams.set("owner", selected);
      url.hash = "#tasks";
    } else {
      url.searchParams.delete("owner");
    }
    window.history.replaceState({}, "", url.toString());
  }

  function setSelectedTaskDueFilter(filter) {
    var selected = text(filter).trim().toLowerCase();
    var url = new URL(window.location.href);
    if (selected === "today") {
      url.searchParams.set("due", "today");
      url.hash = "#tasks";
    } else {
      url.searchParams.delete("due");
    }
    window.history.replaceState({}, "", url.toString());
  }

  function taskOwnerHref(owner, dueFilter) {
    var selected = normalizeTaskOwner(owner);
    var url = new URL(window.location.href);
    url.searchParams.delete("tag");
    if (selected && selected !== "all") {
      url.searchParams.set("owner", selected);
    } else {
      url.searchParams.delete("owner");
    }
    if (dueFilter === "today") {
      url.searchParams.set("due", "today");
    } else {
      url.searchParams.delete("due");
    }
    url.hash = "#tasks";
    return url.pathname + url.search + url.hash;
  }

  function parseClockMinutes(value) {
    var match = /^(\d{1,2})(?::(\d{2}))?$/.exec(text(value).trim());
    if (!match) return null;
    var hour = Number(match[1]);
    var minute = match[2] === undefined ? 0 : Number(match[2]);
    if (hour < 0 || hour > 23 || minute < 0 || minute > 59) return null;
    return hour * 60 + minute;
  }

  function formatClockMinutes(minutes) {
    var normalized = ((minutes % 1440) + 1440) % 1440;
    var hour24 = Math.floor(normalized / 60);
    var minute = normalized % 60;
    var period = hour24 >= 12 ? "PM" : "AM";
    var hour12 = hour24 % 12 || 12;
    return hour12 + ":" + String(minute).padStart(2, "0") + " " + period;
  }

  function formatPlainDate(value) {
    if (!value) return "";
    var date = new Date(value + "T00:00:00");
    if (Number.isNaN(date.getTime())) return value;
    return date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  }

  function formatWeekday(value) {
    if (!value) return "";
    var date = new Date(value + "T00:00:00");
    if (Number.isNaN(date.getTime())) return value.slice(0, 3).toUpperCase();
    return date.toLocaleDateString(undefined, { weekday: "short" }).toUpperCase();
  }

  function isoDateKey(date) {
    var year = date.getFullYear();
    var month = String(date.getMonth() + 1).padStart(2, "0");
    var day = String(date.getDate()).padStart(2, "0");
    return year + "-" + month + "-" + day;
  }

  function parseWakeLockPolicy() {
    var raw = text(getQueryParam("wake"), defaultWakeLockWindow).trim().toLowerCase();
    if (raw === "off" || raw === "false" || raw === "0") {
      return { mode: "off", label: "off" };
    }
    if (raw === "always" || raw === "on" || raw === "true" || raw === "1") {
      return { mode: "always", label: "always" };
    }

    var parts = raw.split("-");
    var start = parts.length === 2 ? parseClockMinutes(parts[0]) : null;
    var end = parts.length === 2 ? parseClockMinutes(parts[1]) : null;
    if (start === null || end === null) {
      parts = defaultWakeLockWindow.split("-");
      start = parseClockMinutes(parts[0]);
      end = parseClockMinutes(parts[1]);
    }

    return {
      mode: "window",
      start: start,
      end: end,
      label: formatClockMinutes(start) + "-" + formatClockMinutes(end),
    };
  }

  function isInsideWakeWindow(now) {
    if (wakeLockPolicy.mode === "always") return true;
    if (wakeLockPolicy.mode !== "window") return false;
    var current = now.getHours() * 60 + now.getMinutes();
    if (wakeLockPolicy.start === wakeLockPolicy.end) return true;
    if (wakeLockPolicy.start < wakeLockPolicy.end) {
      return current >= wakeLockPolicy.start && current < wakeLockPolicy.end;
    }
    return current >= wakeLockPolicy.start || current < wakeLockPolicy.end;
  }

  function shouldHoldWakeLock() {
    return document.visibilityState === "visible" && isInsideWakeWindow(new Date());
  }

  function setWakeLockStatus(label, state, actionLabel) {
    var node = byId("screen-status");
    if (node) {
      node.textContent = label;
      node.dataset.state = state;
    }
    var button = byId("screen-wake-button");
    if (button) {
      button.hidden = !actionLabel;
      if (actionLabel) button.textContent = actionLabel;
    }
  }

  async function releaseNativeWakeLock() {
    if (wakeLockSentinel) {
      var sentinel = wakeLockSentinel;
      wakeLockSentinel = null;
      try {
        await sentinel.release();
      } catch (_error) {
        // The browser may already have released it during tab or device state changes.
      }
    }
  }

  function drawKeepAliveFrame() {
    if (!keepAliveCanvas) return;
    var context = keepAliveCanvas.getContext("2d");
    if (!context) return;
    keepAliveFrame += 1;
    context.fillStyle = keepAliveFrame % 2 === 0 ? "#ffffff" : "#fef7ef";
    context.fillRect(0, 0, keepAliveCanvas.width, keepAliveCanvas.height);
  }

  function startKeepAliveFrames() {
    drawKeepAliveFrame();
    if (!keepAliveFrameTimer) {
      keepAliveFrameTimer = window.setInterval(drawKeepAliveFrame, 30 * 1000);
    }
  }

  function stopVideoKeepAlive() {
    if (keepAliveFrameTimer) {
      window.clearInterval(keepAliveFrameTimer);
      keepAliveFrameTimer = null;
    }
    if (keepAliveVideo) {
      keepAliveVideo.pause();
    }
  }

  function ensureKeepAliveVideo() {
    if (keepAliveVideo) return keepAliveVideo;
    if (!document.createElement) return null;

    var canvas = document.createElement("canvas");
    if (!canvas.getContext || !canvas.captureStream) return null;

    var video = document.createElement("video");
    canvas.width = 2;
    canvas.height = 2;
    video.autoplay = true;
    video.muted = true;
    video.playsInline = true;
    video.className = "screen-keepalive-media";
    video.setAttribute("aria-hidden", "true");
    video.setAttribute("muted", "");
    video.setAttribute("playsinline", "");
    video.srcObject = canvas.captureStream(1);
    document.body.appendChild(video);

    keepAliveCanvas = canvas;
    keepAliveVideo = video;
    return keepAliveVideo;
  }

  async function startVideoKeepAlive() {
    var video = ensureKeepAliveVideo();
    if (!video) {
      var label =
        window.isSecureContext === false ? "Screen wake needs HTTPS" : "Screen wake unsupported";
      setWakeLockStatus(label, "unsupported");
      return false;
    }

    startKeepAliveFrames();
    try {
      var played = video.play();
      if (played && typeof played.then === "function") {
        await played;
      }
      setWakeLockStatus("Screen keepalive on", "fallback");
      return true;
    } catch (_error) {
      stopVideoKeepAlive();
      setWakeLockStatus("Tap for screen keepalive", "waiting", "Keep screen on");
      return false;
    }
  }

  async function releaseWakeLock(label, state) {
    await releaseNativeWakeLock();
    stopVideoKeepAlive();
    setWakeLockStatus(label, state);
  }

  async function syncWakeLock() {
    if (wakeLockPolicy.mode === "off") {
      await releaseWakeLock("Screen wake off", "waiting");
      return;
    }
    if (!shouldHoldWakeLock()) {
      await releaseWakeLock("Screen wake " + wakeLockPolicy.label, "waiting");
      return;
    }
    if (wakeLockSentinel) {
      setWakeLockStatus("Screen staying on", "active");
      return;
    }
    if (keepAliveVideo && !keepAliveVideo.paused) {
      setWakeLockStatus("Screen keepalive on", "fallback");
      return;
    }

    if (navigator.wakeLock && typeof navigator.wakeLock.request === "function") {
      try {
        wakeLockSentinel = await navigator.wakeLock.request("screen");
        wakeLockSentinel.addEventListener("release", function () {
          wakeLockSentinel = null;
          setWakeLockStatus("Screen wake released", "waiting");
        });
        setWakeLockStatus("Screen staying on", "active");
        return;
      } catch (_error) {
        await startVideoKeepAlive();
        return;
      }
    }

    await startVideoKeepAlive();
  }

  function listItem(title, detail) {
    return (
      '<div class="list-item"><strong>' +
      escapeHtml(title) +
      "</strong><span>" +
      escapeHtml(detail || "") +
      "</span></div>"
    );
  }

  function renderReasons(reasons) {
    var node = byId("best-action-reasons");
    if (!node) return;
    if (!reasons || !reasons.length) {
      node.innerHTML = "<span>Fits the current moment</span>";
      return;
    }
    node.innerHTML = reasons
      .map(function (reason) {
        return "<span>" + escapeHtml(reason) + "</span>";
      })
      .join("");
  }

  function renderTimeline(events) {
    var node = byId("today-timeline");
    if (!node) return;
    events = events || [];
    setCardHidden("timeline", !events.length);
    if (!events.length) {
      node.innerHTML = "";
      return;
    }
    node.innerHTML = events
      .map(function (event) {
        var detail = [event.person, event.owner_label, event.location].filter(Boolean).join(" | ");
        return (
          '<div class="timeline-item"><p class="timeline-time">' +
          escapeHtml(event.time_label) +
          '</p><p class="timeline-title">' +
          escapeHtml(event.title) +
          '</p><p class="timeline-detail">' +
          escapeHtml(detail) +
          "</p></div>"
        );
      })
      .join("");
  }

  function renderHomeBoardPersonGroups(items) {
    var grouped = {};
    items.forEach(function (item) {
      var person = text(item.person_or_group, "Family");
      if (!grouped[person]) grouped[person] = [];
      grouped[person].push(item);
    });

    return Object.keys(grouped)
      .sort()
      .map(function (person) {
        var rows = grouped[person]
          .map(function (item) {
            var priority = classToken(item.priority, "medium");
            return (
              '<div class="home-board-item priority-' +
              escapeHtml(priority) +
              '"><span class="home-board-check" aria-hidden="true"></span><div><strong>' +
              escapeHtml(item.message) +
              "</strong><span>" +
              escapeHtml(item.context_label || "General") +
              "</span></div></div>"
            );
          })
          .join("");
        return (
          '<section class="home-board-group"><h4>' +
          escapeHtml(person) +
          '</h4><div class="home-board-group-items">' +
          rows +
          "</div></section>"
        );
      })
      .join("");
  }

  function renderHomeBoard(homeBoard) {
    var node = byId("home-board-items");
    if (!node) return;
    homeBoard = homeBoard || {};
    var todayItems = homeBoard.today || [];
    var tomorrowItems = homeBoard.tomorrow || [];
    var totalCount = todayItems.length + tomorrowItems.length;
    setText(
      "home-board-count",
      todayItems.length + " today | " + tomorrowItems.length + " tomorrow",
    );
    setCardHidden("home-board", !totalCount);
    if (!totalCount) {
      node.innerHTML = "";
      return;
    }

    node.innerHTML = [
      { label: "Today", items: todayItems },
      { label: "Preparing for Tomorrow", items: tomorrowItems },
    ]
      .filter(function (section) {
        return section.items.length;
      })
      .map(function (section) {
        return (
          '<section class="home-board-day"><div class="home-board-day-header"><h4>' +
          escapeHtml(section.label) +
          '</h4><span>' +
          escapeHtml(section.items.length + " pending") +
          '</span></div><div class="home-board-day-groups">' +
          renderHomeBoardPersonGroups(section.items) +
          "</div></section>"
        );
      })
      .join("");
  }

  function bedtimeVoiceStorageKey() {
    return "n4os-dashboard-bedtime-voice";
  }

  function bedtimeStepMinutes(step) {
    return parseClockMinutes(step.time || "");
  }

  function currentClockMinutes() {
    var now = new Date();
    return now.getHours() * 60 + now.getMinutes();
  }

  function setBedtimeStatus(message, state) {
    var node = byId("bedtime-action-status");
    if (!node) return;
    node.textContent = text(message);
    node.dataset.state = state || "";
  }

  function setBedtimeVoiceButton() {
    var button = byId("bedtime-voice-button");
    if (!button) return;
    button.textContent = bedtimeVoiceEnabled ? "Voice on" : "Enable voice";
    button.classList.toggle("is-called-out", bedtimeVoiceEnabled);
    button.setAttribute("aria-pressed", bedtimeVoiceEnabled ? "true" : "false");
  }

  function speakBedtimeNudge(message) {
    if (!bedtimeVoiceEnabled) return false;
    if (!("speechSynthesis" in window) || typeof window.SpeechSynthesisUtterance !== "function") {
      setBedtimeStatus("Voice is not supported in this browser.", "error");
      return false;
    }
    try {
      var utterance = new window.SpeechSynthesisUtterance(message);
      utterance.rate = 0.98;
      utterance.pitch = 1.02;
      utterance.volume = 0.82;
      window.speechSynthesis.cancel();
      window.speechSynthesis.speak(utterance);
      return true;
    } catch (_error) {
      setBedtimeVoiceEnabled(false);
      setBedtimeStatus("Voice failed in this browser, so it was turned off.", "error");
      return false;
    }
  }

  function setBedtimeVoiceEnabled(enabled) {
    bedtimeVoiceEnabled = !!enabled;
    try {
      window.localStorage.setItem(bedtimeVoiceStorageKey(), bedtimeVoiceEnabled ? "1" : "0");
    } catch (_error) {}
    setBedtimeVoiceButton();
  }

  function loadBedtimeVoicePreference() {
    bedtimeVoiceEnabled = false;
    try {
      window.localStorage.setItem(bedtimeVoiceStorageKey(), "0");
    } catch (_error) {}
    setBedtimeVoiceButton();
  }

  function renderBedtime(data) {
    data = data || {};
    lastBedtimeData = data;
    setCardHidden("bedtime", false);
    setText("bedtime-target", data.target || "7:15 PM upstairs");
    setText("bedtime-status", data.enabled ? data.status || "School-night routine" : "Off tonight");
    setBedtimeVoiceButton();

    var node = byId("bedtime-steps");
    if (!node) return;
    var steps = data.steps || [];
    if (!data.enabled) {
      node.innerHTML = '<p class="empty">No bedtime launch routine tonight.</p>';
      return;
    }
    if (!steps.length) {
      node.innerHTML = '<p class="empty">No bedtime steps configured.</p>';
      return;
    }

    var currentMinutes = currentClockMinutes();
    node.innerHTML = steps
      .map(function (step) {
        var stepMinutes = bedtimeStepMinutes(step);
        var active = stepMinutes !== null && !step.acknowledged && currentMinutes >= stepMinutes;
        var ackLabel = step.acknowledged
          ? "Acked " + new Date(step.acked_at).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })
          : "Ack";
        return (
          '<div class="bedtime-step' +
          (active ? " is-active" : "") +
          (step.acknowledged ? " is-acked" : "") +
          '"><div><span class="bedtime-time">' +
          escapeHtml(formatClockMinutes(stepMinutes || 0)) +
          "</span><strong>" +
          escapeHtml(step.label || "Bedtime step") +
          "</strong><p>" +
          escapeHtml(step.detail || "") +
          '</p></div><button class="bedtime-ack-button" type="button" data-bedtime-ack="' +
          escapeHtml(step.id || "") +
          '"' +
          (step.acknowledged ? " disabled" : "") +
          ">" +
          escapeHtml(ackLabel) +
          "</button></div>"
        );
      })
      .join("");
  }

  function postBedtimeAck(stepId, button) {
    if (!stepId) return;
    if (button) {
      button.disabled = true;
      button.textContent = "Acking";
    }
    setBedtimeStatus("Acknowledging bedtime step...", "pending");

    var request = new XMLHttpRequest();
    request.open("POST", "/api/bedtime/ack", true);
    request.setRequestHeader("Content-Type", "application/json");
    request.setRequestHeader("X-N4OS-Dashboard-Action-Token", actionToken());
    request.onreadystatechange = function () {
      if (request.readyState !== 4) return;
      var response = {};
      try {
        response = JSON.parse(request.responseText || "{}");
      } catch (_error) {
        response = {};
      }
      if (request.status >= 200 && request.status < 300 && response.status === "ok") {
        setBedtimeStatus("Bedtime step acknowledged.", "ok");
        if (response.data && response.data.bedtime) {
          renderBedtime(response.data.bedtime);
        } else {
          loadDashboard();
        }
        return;
      }
      setBedtimeStatus(response.message || "Bedtime ack failed.", "error");
      if (button) {
        button.disabled = false;
        button.textContent = "Ack";
      }
    };
    request.send(JSON.stringify({ step_id: stepId }));
  }

  function readingGardenWeekDays(history, recentEvents, fallbackActiveDays, readToday) {
    var countByDate = {};
    (history.heatmap || []).forEach(function (day) {
      if (day.date) countByDate[day.date] = Number(day.count || 0);
    });
    recentEvents.forEach(function (event) {
      if (event.date && !countByDate[event.date]) countByDate[event.date] = 0;
      if (event.date) countByDate[event.date] += 1;
    });

    var today = new Date();
    var days = [];
    for (var offset = 6; offset >= 0; offset -= 1) {
      var date = new Date(today);
      date.setDate(today.getDate() - offset);
      var key = isoDateKey(date);
      days.push({
        date: key,
        label: formatWeekday(key),
        count: Number(countByDate[key] || 0),
        today: offset === 0,
      });
    }

    if (readToday && !days[days.length - 1].count) {
      days[days.length - 1].count = 1;
    }

    var hasReadingDays = days.some(function (day) {
      return day.count > 0;
    });
    var activeDays = Math.max(0, Math.min(Number(fallbackActiveDays || 0), days.length));
    if (!hasReadingDays && activeDays > 0) {
      days.slice(days.length - activeDays).forEach(function (day) {
        day.count = 1;
      });
    }
    return days;
  }

  function renderReadingGardenPlot(history, recentEvents, fallbackActiveDays, readToday) {
    var plot = byId("reading-garden-plot");
    if (!plot) return;
    var days = readingGardenWeekDays(
      history || {},
      recentEvents || [],
      fallbackActiveDays,
      readToday,
    );
    plot.innerHTML = days
      .map(function (day) {
        var count = Number(day.count || 0);
        var level = count >= 3 ? 3 : count >= 2 ? 2 : count >= 1 ? 1 : 0;
        return (
          '<div class="reading-garden-day' +
          (day.today ? " is-today" : "") +
          '" data-level="' +
          level +
          '" title="' +
          escapeHtml(
            formatPlainDate(day.date) + ": " + count + (count === 1 ? " moment" : " moments"),
          ) +
          '"><span class="reading-growth" aria-hidden="true"></span><strong>' +
          escapeHtml(day.label) +
          "</strong></div>"
        );
      })
      .join("");
  }

  function readingBadgeIconClass(label) {
    var normalized = text(label).toLowerCase();
    if (normalized.indexOf("explorer") !== -1 || normalized.indexOf("look") !== -1)
      return "is-explorer";
    if (normalized.indexOf("reader") !== -1 || normalized.indexOf("star") !== -1)
      return "is-reader";
    if (normalized.indexOf("streak") !== -1) return "is-streak";
    return "is-sprout";
  }

  function readingEventDetail(event) {
    var parts = [];
    if (event.date) parts.push(formatPlainDate(event.date));
    if (event.pages) parts.push(event.pages + " pages");
    if (event.minutes) parts.push(event.minutes + " minutes");
    if (event.reading_mode === "read_together") parts.push("read together");
    if (event.reading_mode === "read_aloud") parts.push("read aloud");
    if (event.status === "completed") parts.push("finished");
    if (!parts.length) parts.push("reading moment");
    return parts.join(" | ");
  }

  function selectedReadingSummary(data) {
    data = data || {};
    var byChild = data.by_child || {};
    var childKeys = Object.keys(byChild);
    if (selectedReadingChild === "Family" && data.family) return data.family;
    if (byChild[selectedReadingChild]) return byChild[selectedReadingChild];
    if (childKeys.length) {
      selectedReadingChild = childKeys[0];
      return byChild[selectedReadingChild];
    }
    return data;
  }

  function renderReadingChildTabs(data) {
    var node = byId("reading-child-tabs");
    if (!node) return;
    var byChild = data.by_child || {};
    var children = (data.children || Object.keys(byChild)).slice();
    if (data.family) children.push("Family");
    node.innerHTML = children
      .map(function (child) {
        var active = child === selectedReadingChild ? " is-active" : "";
        return (
          '<button class="reading-child-tab' +
          active +
          '" type="button" data-reading-child="' +
          escapeHtml(child) +
          '">' +
          escapeHtml(child) +
          "</button>"
        );
      })
      .join("");
  }

  function readingCollectionBooks(data) {
    var finished = data.finished || {};
    var books = [];
    var seen = {};
    function addBook(title, status) {
      var cleaned = text(title).trim();
      var key = cleaned.toLowerCase();
      if (!cleaned || cleaned === "unknown book" || seen[key]) return;
      seen[key] = true;
      books.push({ title: cleaned, status: status });
    }
    (data.book_collection || []).forEach(function (book) {
      addBook(book.title, book.status || "Reading");
    });
    addBook(data.current_book, "Current");
    (finished.recent_books || []).forEach(function (book) {
      addBook(book, "Finished");
    });
    (data.recent_events || []).forEach(function (event) {
      addBook(event.book, event.status === "completed" ? "Finished" : "Reading");
    });
    return books;
  }

  function renderBookCollection(data) {
    var node = byId("reading-book-collection");
    var countNode = byId("reading-collection-count");
    if (!node) return;
    var books = readingCollectionBooks(data);
    if (countNode) {
      countNode.textContent = books.length + (books.length === 1 ? " book" : " books");
    }
    setCardHidden("reading-collection", !books.length);
    if (!books.length) {
      node.innerHTML = "";
      return;
    }
    node.innerHTML = books
      .slice(0, 10)
      .map(function (book, index) {
        var initial = book.title.slice(0, 1).toUpperCase();
        var tone =
          index % 3 === 0 ? "is-primary" : index % 3 === 1 ? "is-secondary" : "is-tertiary";
        return (
          '<div class="reading-book-tile"><div class="reading-book-mini-cover ' +
          tone +
          '"><span>' +
          escapeHtml(initial) +
          "</span></div><strong>" +
          escapeHtml(book.title) +
          "</strong><span>" +
          escapeHtml(book.status) +
          "</span></div>"
        );
      })
      .join("");
  }

  function renderReadingGarden(data) {
    data = data || {};
    lastReadingData = data;
    renderReadingChildTabs(data);
    data = selectedReadingSummary(data);
    var today = data.today || {};
    var week = data.week || {};
    var finished = data.finished || {};
    var weeklyGoal = data.weekly_goal || {};
    var streaks = data.streaks || {};
    var history = data.history || {};
    var recentEvents = data.recent_events || [];
    var photos = data.recent_photos || [];
    var currentBook = data.current_book || "unknown book";
    var momentCount = Number(week.reading_moments || 0);
    var pageCount = Number(week.pages || 0);
    var minuteCount = Number(week.minutes || 0);
    var readingDays = Number(week.reading_days || weeklyGoal.reading_days || 0);
    var inferredReadingDays = readingDays || (momentCount > 0 ? 1 : 0);
    var weeklyTarget = Number(weeklyGoal.target_days || 5);
    var inferredRingPercent =
      weeklyTarget > 0 ? Math.round(Math.min(1, inferredReadingDays / weeklyTarget) * 100) : 0;
    setText(
      "reading-garden-title",
      selectedReadingChild === "Family" ? "Family Reading Garden" : "Reading Garden Kid Dashboard",
    );
    setText("reading-today-label", today.label || "Not yet today");
    setText(
      "reading-hero-week",
      weeklyGoal.label || inferredReadingDays + " reading days this week",
    );
    setText(
      "reading-streak-label",
      Number(streaks.current || 0) +
        (Number(streaks.current || 0) === 1 ? " day streak" : " day streak"),
    );
    setText(
      "reading-hero-message",
      today.read
        ? "Your garden grew today. Every page counts."
        : "A reading moment can grow the garden today.",
    );
    setText("reading-current-book", currentBook);
    setText(
      "reading-current-detail",
      currentBook === "unknown book"
        ? "Waiting for the next independent read"
        : "Latest independent reading book",
    );
    setText("reading-week-moments", momentCount);
    setText("reading-week-pages", pageCount);
    setText("reading-week-minutes", minuteCount);
    setText("reading-finished-count", finished.count || 0);
    setText(
      "reading-moment-count",
      recentEvents.length + (recentEvents.length === 1 ? " moment" : " moments"),
    );
    setText("reading-bloomed-count", finished.count || 0);
    setText("reading-current-streak", streaks.current || 0);
    setText("reading-best-streak", streaks.best || 0);
    var progress = Math.max(
      8,
      Math.min(100, momentCount * 18 + (pageCount > 0 ? 18 : 0) + (minuteCount > 0 ? 14 : 0)),
    );
    var progressFill = byId("reading-progress-fill");
    if (progressFill) {
      progressFill.style.width = progress + "%";
    }
    var ringPercent = Number(weeklyGoal.percent || inferredRingPercent);
    var ring = byId("reading-week-ring-fill");
    if (ring) {
      ring.style.setProperty(
        "--reading-ring-percent",
        Math.max(0, Math.min(100, ringPercent)) + "%",
      );
    }
    setText("reading-week-ring-percent", inferredReadingDays + " of " + weeklyTarget + " days");
    setText("reading-week-ring-label", "Weekly progress");

    var coverNode = byId("reading-current-cover");
    var coverPhoto = photos.length ? photos[0] : null;
    if (coverNode) {
      if (coverPhoto && coverPhoto.path) {
        coverNode.style.backgroundImage =
          'url("' + String(coverPhoto.path).replace(/"/g, "%22") + '")';
        coverNode.classList.add("has-photo");
      } else {
        coverNode.style.backgroundImage = "";
        coverNode.classList.remove("has-photo");
      }
    }

    var reactionNode = byId("reading-favorite-reaction");
    if (reactionNode) {
      reactionNode.textContent = data.favorite_reaction
        ? "Favorite discovery: " + data.favorite_reaction
        : "";
      reactionNode.hidden = !data.favorite_reaction;
    }

    renderReadingGardenPlot(history, recentEvents, inferredReadingDays, !!today.read);

    var finishedNode = byId("reading-finished-books");
    var recentBooks = finished.recent_books || [];
    setCardHidden("reading-bloomed", !recentBooks.length);
    if (finishedNode) {
      finishedNode.innerHTML = recentBooks.length
        ? recentBooks
            .slice(0, 4)
            .map(function (book) {
              return (
                '<div class="reading-bloomed-item"><span class="garden-token is-flower" aria-hidden="true"></span><div><strong>' +
                escapeHtml(book) +
                "</strong><span>I read it myself</span></div></div>"
              );
            })
            .join("")
        : "";
    }

    var badges = data.badges || [];
    var earnedBadges = badges.filter(function (badge) {
      return badge.earned;
    });
    setText("reading-badge-count", earnedBadges.length + " earned");
    var badgeNode = byId("reading-badges");
    if (badgeNode) {
      badgeNode.innerHTML = badges.length
        ? badges
            .map(function (badge) {
              var iconClass = readingBadgeIconClass(badge.label || "");
              var badgeDetail = text(
                badge.detail ||
                  (badge.earned ? "Celebrating your reading." : "Keep reading to unlock."),
              ).slice(0, 84);
              return (
                '<div class="reading-badge' +
                (badge.earned ? " is-earned" : "") +
                '" title="' +
                escapeHtml(badge.detail || "") +
                '"><span class="reading-badge-icon ' +
                iconClass +
                '" aria-hidden="true"></span><div><strong>' +
                escapeHtml(badge.label || "") +
                "</strong><span>" +
                escapeHtml(badgeDetail) +
                "</span></div></div>"
              );
            })
            .join("")
        : "";
    }

    var photoNode = byId("reading-photo-list");
    setText("reading-photo-count", photos.length);
    setCardHidden("reading-photos", !photos.length);
    if (photoNode) {
      photoNode.innerHTML = photos.length
        ? photos
            .slice(0, 6)
            .map(function (photo) {
              var book = photo.book || "Book photo";
              if (photo.path) {
                return (
                  '<div class="reading-photo-item"><img src="' +
                  escapeHtml(photo.path) +
                  '" alt="' +
                  escapeHtml(book) +
                  '"><span>' +
                  escapeHtml(book) +
                  "</span></div>"
                );
              }
              return listItem(book, "");
            })
            .join("")
        : "";
    }

    var eventsNode = byId("reading-recent-events");
    setCardHidden("reading-moments", !recentEvents.length);
    if (eventsNode) {
      eventsNode.innerHTML = recentEvents.length
        ? recentEvents
            .slice(0, 6)
            .map(function (event) {
              var title =
                event.book && event.book !== "unknown book" ? event.book : "Reading moment";
              return (
                '<div class="reading-event-item"><span class="garden-token is-sprout" aria-hidden="true"></span><div><strong>' +
                escapeHtml(title) +
                "</strong><span>" +
                escapeHtml(readingEventDetail(event)) +
                '</span><span class="reading-event-mode">' +
                escapeHtml(readingModeLabel(event.reading_mode)) +
                "</span>" +
                readingEventEditControls(event) +
                "</div></div>"
              );
            })
            .join("")
        : "";
    }

    renderLibraryBag(data.library_visit || {}, data.current_bag || {});
    renderBookCollection(data);
    renderReadingHistory(history);
  }

  function renderReadingHistory(history) {
    history = history || {};
    var monthly = history.monthly || {};
    setText(
      "reading-month-summary",
      Number(monthly.reading_days || 0) +
        (Number(monthly.reading_days || 0) === 1 ? " reading day" : " reading days") +
        " this month",
    );
    var node = byId("reading-heatmap");
    if (!node) return;
    var days = history.heatmap || [];
    setCardHidden("reading-history", !days.length);
    node.innerHTML = days
      .map(function (day) {
        var count = Number(day.count || 0);
        var level = count >= 3 ? 3 : count >= 2 ? 2 : count >= 1 ? 1 : 0;
        var label =
          formatPlainDate(day.date) + ": " + count + (count === 1 ? " moment" : " moments");
        return (
          '<button type="button" class="reading-heatmap-day" data-reading-heatmap-day data-date="' +
          escapeHtml(day.date) +
          '" data-count="' +
          count +
          '" data-level="' +
          level +
          '" title="' +
          escapeHtml(label) +
          '" aria-label="' +
          escapeHtml(label) +
          '"></button>'
        );
      })
      .join("");
    setText("reading-heatmap-detail", "Tap a day to see reading details.");
  }

  function showReadingHeatmapDetail(target) {
    var date = target.getAttribute("data-date") || "";
    var count = Number(target.getAttribute("data-count") || 0);
    setText(
      "reading-heatmap-detail",
      formatPlainDate(date) + ": " + count + (count === 1 ? " reading moment" : " reading moments"),
    );
  }

  function renderLibraryBag(visit, bag) {
    visit = visit || {};
    bag = bag || {};
    var count = Number(bag.count || 0);
    var days = visit.days_since_visit;
    var daysLabel = visit.has_visit
      ? days === 0
        ? "Today"
        : days + (days === 1 ? " day ago" : " days ago")
      : "Not started";
    setText("library-visit-days", daysLabel);
    setText(
      "library-visit-label",
      visit.label || "Paste a library checkout email to start your library bag.",
    );
    setText("library-bag-count", count + (count === 1 ? " book" : " books"));

    var dueNode = byId("library-due-date");
    var dueDate = bag.due_date || visit.due_date || "";
    if (dueNode) {
      dueNode.textContent = dueDate ? "Due " + formatPlainDate(dueDate) : "";
      dueNode.hidden = !dueDate;
    }

    var listNode = byId("library-bag-list");
    var titles = bag.titles || [];
    setCardHidden("library-bag", !titles.length);
    if (!listNode) return;
    listNode.innerHTML = titles.length
      ? titles
          .slice(0, 12)
          .map(function (title) {
            return (
              '<div class="library-bag-item"><span class="garden-token is-leaf" aria-hidden="true"></span><strong>' +
              escapeHtml(title) +
              "</strong></div>"
            );
          })
          .join("")
      : "";
  }

  function renderSummary(data) {
    var summary = data.summary || {};
    var family = data.family || {};
    setSummaryChip("summary-prep", summary.prep_needed_count, "prep", "prep");
    setSummaryChip("summary-decisions", summary.open_decision_count, "decision", "decisions");
    setSummaryChip("summary-home", summary.home_board_count, "home", "home");
    setSummaryChip(
      "summary-family",
      (family.responsibilities || []).length +
        (family.child_events || []).length +
        (family.unassigned || []).length,
      "family",
      "family",
    );
    renderTaskOwnerChips((data.tasks || {}).owners || []);
  }

  function renderTaskOwnerChips(owners) {
    var node = byId("task-owner-chips");
    if (!node) return;
    var byOwner = {};
    (owners || []).forEach(function (entry) {
      byOwner[normalizeTaskOwner(entry.owner)] = {
        owner: normalizeTaskOwner(entry.owner),
        label: text(entry.label) || titleLabel(entry.owner),
        count: Number(entry.count || 0),
        todayCount: Number(entry.today_count || 0),
      };
    });
    ["nysha", "navya"].forEach(function (owner) {
      if (!byOwner[owner]) {
        byOwner[owner] = { owner: owner, label: titleLabel(owner), count: 0, todayCount: 0 };
      }
    });
    var visibleOwners = ["nysha", "navya"]
      .map(function (owner) {
        return byOwner[owner];
      })
      .concat(
        Object.keys(byOwner)
          .filter(function (owner) {
            return owner !== "nysha" && owner !== "navya" && byOwner[owner].todayCount > 0;
          })
          .sort(function (left, right) {
            return byOwner[left].label.localeCompare(byOwner[right].label);
          })
          .map(function (owner) {
            return byOwner[owner];
          }),
      );
    node.innerHTML = visibleOwners
      .map(function (entry) {
        var owner = normalizeTaskOwner(entry.owner);
        var label = text(entry.label) || (owner === "unknown" ? "Unassigned" : titleLabel(owner));
        var todayCount = Number(entry.todayCount || 0);
        var totalCount = Number(entry.count || 0);
        var count = todayCount || totalCount;
        var dueFilter = todayCount > 0 ? "today" : "";
        return (
          '<a class="task-owner-chip ' +
          (owner === "unknown" ? "is-unassigned" : "") +
          (count === 0 ? " is-empty" : "") +
          '" href="' +
          escapeHtml(taskOwnerHref(owner, dueFilter)) +
          '" data-task-owner-chip="' +
          escapeHtml(owner) +
          '" data-task-owner-due="' +
          escapeHtml(dueFilter) +
          '"><strong>' +
          escapeHtml(String(count)) +
          "</strong><span>" +
          escapeHtml(label) +
          (dueFilter === "today" ? " today" : " open") +
          "</span></a>"
        );
      })
      .join("");
  }

  function renderPrep(events) {
    var node = byId("prep-needed");
    if (!node) return;
    events = events || [];
    setCardHidden("prep-needed", !events.length);
    if (!events.length) {
      node.innerHTML = "";
      return;
    }
    node.innerHTML = events
      .slice(0, 3)
      .map(function (event) {
        return listItem(event.title, event.preparation_notes || event.day_label);
      })
      .join("");
  }

  function renderWarnings(warnings) {
    var node = byId("warnings");
    if (!node) return;
    warnings = warnings || [];
    setCardHidden("warnings", !warnings.length);
    if (!warnings.length) {
      node.innerHTML = "";
      return;
    }
    node.innerHTML = warnings
      .slice(0, 3)
      .map(function (warning) {
        return listItem(warning.title, warning.detail);
      })
      .join("");
  }

  function taskBadge(label, tone) {
    if (!label) return "";
    return (
      '<span class="task-badge ' + escapeHtml(tone || "") + '">' + escapeHtml(label) + "</span>"
    );
  }

  function taskTagBadge(tag) {
    var normalized = normalizeTag(tag);
    if (!normalized) return "";
    return (
      '<button class="task-badge tag" type="button" data-task-tag-filter="' +
      escapeHtml(normalized) +
      '">#' +
      escapeHtml(normalized) +
      "</button>"
    );
  }

  function taskMatchesTag(task, tag) {
    if (!tag) return true;
    return (task.tags || []).some(function (taskTag) {
      return normalizeTag(taskTag) === tag;
    });
  }

  function filterTasksByTag(tasks, tag) {
    tasks = tasks || [];
    if (!tag) return tasks;
    return tasks.filter(function (task) {
      return taskMatchesTag(task, tag);
    });
  }

  function taskMatchesOwner(task, owner) {
    return !owner || owner === "all" || normalizeTaskOwner(task.owner) === owner;
  }

  function taskMatchesDueFilter(task, dueFilter) {
    if (dueFilter !== "today") return true;
    return Number(task.days_until_due) === 0;
  }

  function filterTasks(tasks, tag, owner, dueFilter) {
    tasks = tasks || [];
    return tasks.filter(function (task) {
      return (
        taskMatchesTag(task, tag) &&
        taskMatchesOwner(task, owner) &&
        taskMatchesDueFilter(task, dueFilter)
      );
    });
  }

  function renderTaskOwnerFilter(owners, selectedOwner) {
    var node = byId("task-owner-filter");
    if (!node) return;
    selectedOwner = selectedOwner || "all";
    var options = [{ owner: "all", label: "All owners" }];
    var seen = { all: true };
    (owners || []).forEach(function (entry) {
      var owner = normalizeTaskOwner(entry.owner);
      if (seen[owner]) return;
      seen[owner] = true;
      options.push({
        owner: owner,
        label: text(entry.label) || (owner === "unknown" ? "Unassigned" : titleLabel(owner)),
      });
    });
    if (selectedOwner !== "all" && !seen[selectedOwner]) {
      options.push({ owner: selectedOwner, label: titleLabel(selectedOwner) });
    }
    node.innerHTML = options
      .map(function (entry) {
        return (
          '<option value="' +
          escapeHtml(entry.owner) +
          '"' +
          (entry.owner === selectedOwner ? " selected" : "") +
          ">" +
          escapeHtml(entry.label) +
          "</option>"
        );
      })
      .join("");
    node.hidden = options.length <= 1 && selectedOwner === "all";
  }

  function renderTaskTagFilters(tags, selectedTag, taskCount) {
    var node = byId("task-tag-filters");
    if (!node) return;
    tags = (tags || []).map(normalizeTag).filter(Boolean);
    var seen = {};
    tags = tags.filter(function (tag) {
      if (seen[tag]) return false;
      seen[tag] = true;
      return true;
    });
    if (selectedTag && !seen[selectedTag]) {
      tags.unshift(selectedTag);
    }
    if (!tags.length && !selectedTag) {
      node.hidden = true;
      node.innerHTML = "";
      return;
    }
    node.hidden = false;
    var allButton =
      '<button class="task-tag-filter ' +
      (!selectedTag ? "is-active" : "") +
      '" type="button" data-task-tag-filter="">All</button>';
    var tagButtons = tags
      .map(function (tag) {
        return (
          '<button class="task-tag-filter ' +
          (tag === selectedTag ? "is-active" : "") +
          '" type="button" data-task-tag-filter="' +
          escapeHtml(tag) +
          '">#' +
          escapeHtml(tag) +
          "</button>"
        );
      })
      .join("");
    var resultLabel = selectedTag
      ? '<span class="task-tag-result">' + escapeHtml(taskCount) + " shown</span>"
      : "";
    node.innerHTML = allButton + tagButtons + resultLabel;
  }

  function taskTone(task) {
    if (
      task.days_until_due !== null &&
      task.days_until_due !== undefined &&
      task.days_until_due < 0
    ) {
      return "is-overdue";
    }
    if (task.days_until_due === 0) {
      return "is-due-today";
    }
    if (task.owner === "unknown") {
      return "is-unassigned";
    }
    return "is-pending";
  }

  function taskContextLabel(task) {
    var values = []
      .concat(task.context || [])
      .concat(task.requires || [])
      .concat(task.can_do_while || []);
    var seen = {};
    var labels = values.filter(function (value) {
      var key = text(value).toLowerCase();
      if (!key || seen[key]) return false;
      seen[key] = true;
      return true;
    });
    if (labels.length) return labels.slice(0, 2).join(" | ");
    if (task.effort_type && task.effort_type !== "unknown") return task.effort_type;
    return "";
  }

  function setTaskActionStatus(message, state) {
    var node = byId("task-action-status");
    if (!node) return;
    node.textContent = text(message);
    node.dataset.state = state || "";
  }

  function celebrationOwnerName(ownerLabel) {
    var owner = text(ownerLabel).trim();
    return owner && owner !== "Unassigned" ? owner : "Team";
  }

  function speakTaskCelebration(ownerLabel) {
    if (!("speechSynthesis" in window) || typeof window.SpeechSynthesisUtterance !== "function") {
      return;
    }
    var owner = celebrationOwnerName(ownerLabel);
    var utterance = new window.SpeechSynthesisUtterance("Nice work, " + owner + ". Task complete.");
    utterance.rate = 1.02;
    utterance.pitch = 1.08;
    utterance.volume = 0.75;
    window.speechSynthesis.cancel();
    window.speechSynthesis.speak(utterance);
  }

  function showTaskCelebration(taskTitle, ownerLabel) {
    var layer = byId("task-celebration");
    if (!layer) return;
    var titleNode = byId("task-celebration-title");
    var ownerNode = byId("task-celebration-owner");
    var burstsNode = byId("task-celebration-bursts");
    var owner = celebrationOwnerName(ownerLabel);
    if (ownerNode) ownerNode.textContent = owner + " finished a task";
    if (titleNode) titleNode.textContent = text(taskTitle, "Nice work.");
    if (burstsNode) {
      burstsNode.innerHTML = Array.from({ length: 22 })
        .map(function (_, index) {
          return '<span style="--i:' + index + '"></span>';
        })
        .join("");
    }
    layer.hidden = false;
    layer.classList.remove("is-active");
    window.requestAnimationFrame(function () {
      layer.classList.add("is-active");
    });
    window.clearTimeout(layer._hideTimer);
    layer._hideTimer = window.setTimeout(function () {
      layer.classList.remove("is-active");
      layer.hidden = true;
    }, 2600);
    speakTaskCelebration(ownerLabel);
  }

  function setDecisionActionStatus(message, state) {
    var node = byId("decision-action-status");
    if (!node) return;
    node.textContent = text(message);
    node.dataset.state = state || "";
  }

  function setShoppingActionStatus(message, state) {
    var node = byId("shopping-action-status");
    if (!node) return;
    node.textContent = text(message);
    node.dataset.state = state || "";
  }

  function setReadingActionStatus(message, state) {
    var node = byId("reading-action-status");
    if (!node) return;
    node.textContent = text(message);
    node.dataset.state = state || "";
  }

  function actionToken() {
    var node = document.querySelector('meta[name="n4os-dashboard-action-token"]');
    return node ? node.getAttribute("content") || "" : "";
  }

  function parseOptionalNumber(value) {
    var cleaned = text(value).trim();
    if (!cleaned) return null;
    var parsed = Number(cleaned);
    return Number.isFinite(parsed) && parsed >= 0 ? Math.round(parsed) : null;
  }

  function readingModeLabel(value) {
    if (value === "read_together") return "Read together";
    if (value === "read_aloud") return "Read aloud";
    if (value === "independent") return "Independent";
    return "Unknown";
  }

  function readingEventEditControls(event) {
    if (!event.id) return "";
    var pages = event.pages === null || event.pages === undefined ? "" : event.pages;
    var minutes = event.minutes === null || event.minutes === undefined ? "" : event.minutes;
    var status = event.status || "in_progress";
    var mode = event.reading_mode || "unknown";
    return (
      '<details class="reading-edit-panel"><summary>Edit</summary>' +
      '<div class="reading-edit-grid" data-reading-edit-form="' +
      escapeHtml(event.id) +
      '">' +
      '<label>Book<input data-reading-field="book" value="' +
      escapeHtml(event.book || "") +
      '"></label>' +
      '<label>Date<input data-reading-field="date" type="date" value="' +
      escapeHtml(event.date || "") +
      '"></label>' +
      '<label>Pages<input data-reading-field="pages" type="number" min="0" value="' +
      escapeHtml(String(pages)) +
      '"></label>' +
      '<label>Minutes<input data-reading-field="minutes" type="number" min="0" value="' +
      escapeHtml(String(minutes)) +
      '"></label>' +
      '<label>Status<select data-reading-field="status"><option value="in_progress"' +
      (status === "in_progress" ? " selected" : "") +
      '>Reading</option><option value="completed"' +
      (status === "completed" ? " selected" : "") +
      '>Finished</option><option value="unknown"' +
      (status === "unknown" ? " selected" : "") +
      ">Unknown</option></select></label>" +
      '<label>Mode<select data-reading-field="reading_mode"><option value="independent"' +
      (mode === "independent" ? " selected" : "") +
      '>Independent</option><option value="read_together"' +
      (mode === "read_together" ? " selected" : "") +
      '>Read together</option><option value="read_aloud"' +
      (mode === "read_aloud" ? " selected" : "") +
      '>Read aloud</option><option value="unknown"' +
      (mode === "unknown" ? " selected" : "") +
      ">Unknown</option></select></label>" +
      "</div>" +
      '<div class="reading-event-actions"><button class="reading-save-button" type="button" data-reading-update="' +
      escapeHtml(event.id) +
      '">Save</button><button class="reading-delete-button" type="button" data-reading-delete="' +
      escapeHtml(event.id) +
      '">Delete</button></div></details>'
    );
  }

  function readingEventPayload(eventId) {
    var form = null;
    Array.prototype.some.call(
      document.querySelectorAll("[data-reading-edit-form]"),
      function (candidate) {
        if (candidate.getAttribute("data-reading-edit-form") === eventId) {
          form = candidate;
          return true;
        }
        return false;
      },
    );
    if (!form) return null;
    function field(name) {
      var node = form.querySelector('[data-reading-field="' + name + '"]');
      return node ? node.value : "";
    }
    var pagesRaw = field("pages");
    var minutesRaw = field("minutes");
    return {
      event_id: eventId,
      book: field("book"),
      date: field("date"),
      pages: parseOptionalNumber(pagesRaw),
      minutes: parseOptionalNumber(minutesRaw),
      status: field("status"),
      reading_mode: field("reading_mode"),
      clear_pages: text(pagesRaw).trim() === "",
      clear_minutes: text(minutesRaw).trim() === "",
    };
  }

  function postReadingUpdate(eventId, button) {
    var payload = readingEventPayload(eventId);
    if (!payload) return;
    if (button) {
      button.disabled = true;
      button.textContent = "Saving";
    }
    setReadingActionStatus("Updating reading moment...", "pending");

    var request = new XMLHttpRequest();
    request.open("POST", "/api/library/reading/update", true);
    request.setRequestHeader("Content-Type", "application/json");
    request.setRequestHeader("X-N4OS-Dashboard-Action-Token", actionToken());
    request.onreadystatechange = function () {
      if (request.readyState !== 4) return;
      var response = {};
      try {
        response = JSON.parse(request.responseText || "{}");
      } catch (_error) {
        response = {};
      }
      if (request.status >= 200 && request.status < 300 && response.status === "ok") {
        setReadingActionStatus("Reading moment updated.", "ok");
        loadDashboard();
        return;
      }
      setReadingActionStatus(response.message || "Reading update failed.", "error");
      if (button) {
        button.disabled = false;
        button.textContent = "Save";
      }
    };
    request.send(JSON.stringify(payload));
  }

  function postReadingDelete(eventId, button) {
    if (!eventId) return;
    if (!window.confirm("Delete this reading moment?")) return;
    if (button) {
      button.disabled = true;
      button.textContent = "Deleting";
    }
    setReadingActionStatus("Deleting reading moment...", "pending");

    var request = new XMLHttpRequest();
    request.open("POST", "/api/library/reading/delete", true);
    request.setRequestHeader("Content-Type", "application/json");
    request.setRequestHeader("X-N4OS-Dashboard-Action-Token", actionToken());
    request.onreadystatechange = function () {
      if (request.readyState !== 4) return;
      var response = {};
      try {
        response = JSON.parse(request.responseText || "{}");
      } catch (_error) {
        response = {};
      }
      if (request.status >= 200 && request.status < 300 && response.status === "ok") {
        setReadingActionStatus("Reading moment deleted.", "ok");
        loadDashboard();
        return;
      }
      setReadingActionStatus(response.message || "Reading delete failed.", "error");
      if (button) {
        button.disabled = false;
        button.textContent = "Delete";
      }
    };
    request.send(JSON.stringify({ event_id: eventId }));
  }

  function taskCompleteButton(task) {
    if (!task.id) return "";
    return (
      '<button class="task-complete-button" type="button" data-task-complete="' +
      escapeHtml(task.id) +
      '" data-task-title="' +
      escapeHtml(task.title || "Task") +
      '" data-task-owner-label="' +
      escapeHtml(task.owner_label || "") +
      '" aria-label="Complete ' +
      escapeHtml(task.title) +
      '">Complete</button>'
    );
  }

  function postCompleteTask(taskId, button) {
    if (!taskId) return;
    if (button) {
      button.disabled = true;
      button.textContent = "Completing";
    }
    setTaskActionStatus("Completing task...", "pending");

    var request = new XMLHttpRequest();
    request.open("POST", "/api/tasks/complete", true);
    request.setRequestHeader("Content-Type", "application/json");
    request.setRequestHeader("X-N4OS-Dashboard-Action-Token", actionToken());
    request.onreadystatechange = function () {
      if (request.readyState !== 4) return;
      var payload = {};
      try {
        payload = JSON.parse(request.responseText || "{}");
      } catch (_error) {
        payload = {};
      }
      if (request.status >= 200 && request.status < 300 && payload.status === "ok") {
        setTaskActionStatus("Task completed.", "ok");
        showTaskCelebration(
          button ? button.getAttribute("data-task-title") : "",
          button ? button.getAttribute("data-task-owner-label") : "",
        );
        loadDashboard();
        return;
      }
      var message = payload.message || "Task completion failed.";
      setTaskActionStatus(message, "error");
      if (button) {
        button.disabled = false;
        button.textContent = "Complete";
      }
    };
    request.send(JSON.stringify({ task_id: taskId }));
  }

  function decisionCompleteButton(decision) {
    if (!decision.id) return "";
    return (
      '<button class="decision-complete-button" type="button" data-decision-complete="' +
      escapeHtml(decision.id) +
      '" aria-label="Mark decision done: ' +
      escapeHtml(decision.title) +
      '">Done</button>'
    );
  }

  function postCompleteDecision(decisionId, button) {
    if (!decisionId) return;
    if (button) {
      button.disabled = true;
      button.textContent = "Saving";
    }
    setDecisionActionStatus("Marking decision done...", "pending");

    var request = new XMLHttpRequest();
    request.open("POST", "/api/decisions/complete", true);
    request.setRequestHeader("Content-Type", "application/json");
    request.setRequestHeader("X-N4OS-Dashboard-Action-Token", actionToken());
    request.onreadystatechange = function () {
      if (request.readyState !== 4) return;
      var payload = {};
      try {
        payload = JSON.parse(request.responseText || "{}");
      } catch (_error) {
        payload = {};
      }
      if (request.status >= 200 && request.status < 300 && payload.status === "ok") {
        setDecisionActionStatus("Decision marked done.", "ok");
        loadDashboard();
        return;
      }
      var message = payload.message || "Decision update failed.";
      setDecisionActionStatus(message, "error");
      if (button) {
        button.disabled = false;
        button.textContent = "Done";
      }
    };
    request.send(JSON.stringify({ decision_id: decisionId }));
  }

  function shoppingCheckButton(item) {
    if (!item.id) return "";
    return (
      '<button class="shopping-check-button" type="button" data-shopping-check="' +
      escapeHtml(item.id) +
      '" data-shopping-list="' +
      escapeHtml(item.list_slug || "") +
      '" aria-label="Check off ' +
      escapeHtml(item.title) +
      '">Done</button>'
    );
  }

  function shoppingClearButton(list) {
    return (
      '<button class="shopping-clear-button" type="button" data-shopping-clear="' +
      escapeHtml(list.slug || "") +
      '" aria-label="Clear ' +
      escapeHtml(list.name || "shopping list") +
      '">Clear</button>'
    );
  }

  function postShoppingCheck(itemId, listSlug, button) {
    if (!itemId) return;
    if (button) {
      button.disabled = true;
      button.textContent = "Saving";
    }
    setShoppingActionStatus("Updating shopping item...", "pending");

    var request = new XMLHttpRequest();
    request.open("POST", "/api/shopping/items/check", true);
    request.setRequestHeader("Content-Type", "application/json");
    request.setRequestHeader("X-N4OS-Dashboard-Action-Token", actionToken());
    request.onreadystatechange = function () {
      if (request.readyState !== 4) return;
      var payload = {};
      try {
        payload = JSON.parse(request.responseText || "{}");
      } catch (_error) {
        payload = {};
      }
      if (request.status >= 200 && request.status < 300 && payload.status === "ok") {
        setShoppingActionStatus("Shopping item checked off.", "ok");
        loadDashboard();
        return;
      }
      setShoppingActionStatus(payload.message || "Shopping update failed.", "error");
      if (button) {
        button.disabled = false;
        button.textContent = "Done";
      }
    };
    request.send(JSON.stringify({ item_id: itemId, list_slug: listSlug }));
  }

  function postShoppingClear(listSlug, button) {
    if (!listSlug) return;
    if (button) {
      button.disabled = true;
      button.textContent = "Clearing";
    }
    setShoppingActionStatus("Clearing shopping list...", "pending");

    var request = new XMLHttpRequest();
    request.open("POST", "/api/shopping/lists/clear", true);
    request.setRequestHeader("Content-Type", "application/json");
    request.setRequestHeader("X-N4OS-Dashboard-Action-Token", actionToken());
    request.onreadystatechange = function () {
      if (request.readyState !== 4) return;
      var payload = {};
      try {
        payload = JSON.parse(request.responseText || "{}");
      } catch (_error) {
        payload = {};
      }
      if (request.status >= 200 && request.status < 300 && payload.status === "ok") {
        setShoppingActionStatus(payload.message || "Shopping list cleared.", "ok");
        loadDashboard();
        return;
      }
      setShoppingActionStatus(payload.message || "Shopping clear failed.", "error");
      if (button) {
        button.disabled = false;
        button.textContent = "Clear";
      }
    };
    request.send(JSON.stringify({ list_slug: listSlug }));
  }

  function renderShoppingTabs(lists) {
    var node = byId("shopping-list-tabs");
    if (!node) return;
    lists = lists || [];
    var selectedExists =
      selectedShoppingList === "all" ||
      lists.some(function (list) {
        return list.slug === selectedShoppingList;
      });
    if (!selectedExists) {
      selectedShoppingList = "all";
    }

    var allCount = lists.reduce(function (sum, list) {
      return sum + Number(list.pending_count || 0);
    }, 0);
    var tabs = [{ slug: "all", name: "All", pending_count: allCount }].concat(lists);
    node.innerHTML = tabs
      .map(function (tab) {
        var isSelected = tab.slug === selectedShoppingList;
        return (
          '<button class="shopping-list-tab' +
          (isSelected ? " is-active" : "") +
          '" type="button" role="tab" aria-selected="' +
          (isSelected ? "true" : "false") +
          '" data-shopping-tab="' +
          escapeHtml(tab.slug) +
          '"><span>' +
          escapeHtml(tab.name || tab.slug) +
          "</span><strong>" +
          escapeHtml(String(tab.pending_count || 0)) +
          "</strong></button>"
        );
      })
      .join("");
  }

  function renderShopping(data) {
    data = data || {};
    var byList = data.by_list || [];
    var pending = data.pending || [];
    var node = byId("shopping-list-items");
    lastShoppingData = data;
    setSummaryChip("summary-shopping", pending.length, "item", "items");
    setText(
      "shopping-count-label",
      pending.length + (pending.length === 1 ? " pending" : " pending"),
    );
    renderShoppingTabs(byList);
    if (!node) return;
    if (!byList.length) {
      node.innerHTML = empty("Shopping lists are not available yet.");
      return;
    }

    var visibleLists =
      selectedShoppingList === "all"
        ? byList
        : byList.filter(function (list) {
            return list.slug === selectedShoppingList;
          });
    node.classList.toggle("is-single-list", selectedShoppingList !== "all");
    node.innerHTML = visibleLists
      .map(function (list) {
        var items = list.items || [];
        var rows = items.length
          ? items
              .map(function (item) {
                var detail = [item.quantity, item.category, item.note].filter(Boolean).join(" | ");
                return (
                  '<div class="shopping-item"><div class="shopping-item-main"><strong>' +
                  escapeHtml(item.title) +
                  "</strong><span>" +
                  escapeHtml(detail || list.name) +
                  '</span></div><div class="shopping-item-actions">' +
                  shoppingCheckButton(item) +
                  "</div></div>"
                );
              })
              .join("")
          : empty("Nothing pending.");
        return (
          '<article class="panel shopping-list-card" role="tabpanel" aria-label="' +
          escapeHtml(list.name || list.slug) +
          '"><div class="panel-header"><h3>' +
          escapeHtml(list.name || list.slug) +
          '</h3><div class="shopping-list-actions"><span>' +
          escapeHtml((list.pending_count || 0) + " pending") +
          "</span>" +
          shoppingClearButton(list) +
          '</div></div><div class="shopping-list-card-items">' +
          rows +
          "</div></article>"
        );
      })
      .join("");
  }

  function renderPendingTasks(tasks, allTaskCount, dueFilter) {
    var node = byId("pending-task-items");
    tasks = tasks || [];
    allTaskCount = allTaskCount === undefined ? tasks.length : allTaskCount;
    var dueCount = tasks.filter(function (task) {
      return (
        task.days_until_due !== null &&
        task.days_until_due !== undefined &&
        task.days_until_due <= 0
      );
    }).length;
    var unassignedCount = tasks.filter(function (task) {
      return task.owner === "unknown";
    }).length;
    var unscheduledCount = tasks.filter(function (task) {
      return task.days_until_due === null || task.days_until_due === undefined;
    }).length;
    var countLabel =
      tasks.length === allTaskCount
        ? tasks.length + " pending"
        : tasks.length + " of " + allTaskCount + " pending";
    setText("task-count-label", countLabel);
    setText("pending-task-count", countLabel);
    setText("task-due-count", dueCount);
    setText("task-unassigned-count", unassignedCount);
    setText("task-unscheduled-count", unscheduledCount);
    setCardHidden("pending-tasks", !allTaskCount);
    setCardHidden("task-triage", dueCount + unassignedCount + unscheduledCount === 0);
    if (!node) return;
    if (!tasks.length) {
      node.innerHTML = empty(
        dueFilter === "today"
          ? "No tasks due today match these filters."
          : "No pending tasks match these filters.",
      );
      return;
    }
    node.innerHTML = tasks
      .slice(0, 10)
      .map(function (task) {
        var context = taskContextLabel(task);
        var badges = [
          taskBadge(
            task.due_label || "No due date",
            task.days_until_due !== null &&
              task.days_until_due !== undefined &&
              task.days_until_due <= 0
              ? "alert"
              : "warm",
          ),
          taskBadge(task.owner_label || "Unassigned", task.owner === "unknown" ? "alert" : "green"),
          task.duration_minutes ? taskBadge(task.duration_minutes + " min", "") : "",
          context ? taskBadge(context, "") : "",
        ]
          .concat((task.tags || []).map(taskTagBadge))
          .join("");
        return (
          '<div class="pending-task ' +
          escapeHtml(taskTone(task)) +
          '"><div class="pending-task-main"><strong>' +
          escapeHtml(task.title) +
          '</strong><div class="pending-task-badges">' +
          badges +
          '</div></div><div class="pending-task-actions">' +
          taskCompleteButton(task) +
          "</div></div>"
        );
      })
      .join("");
  }

  function renderTaskGroups(groups, selectedTag) {
    var node = byId("task-groups");
    if (!node) return;
    var visibleGroups = (groups || [])
      .map(function (group) {
        var items = group.items || [];
        if (selectedTag) {
          items = items.filter(function (recommendation) {
            return recommendation.task && taskMatchesTag(recommendation.task, selectedTag);
          });
        }
        return {
          label: group.label,
          detail: group.detail,
          items: items,
        };
      })
      .filter(function (group) {
        return group.items && group.items.length;
      });
    setContainerHidden("task-lanes-heading", !visibleGroups.length);
    setContainerHidden("task-groups", !visibleGroups.length);
    if (!visibleGroups.length) {
      node.innerHTML = "";
      return;
    }
    node.innerHTML = visibleGroups
      .map(function (group) {
        var items = group.items
          .map(function (recommendation) {
            var task = recommendation.task;
            var reason =
              recommendation.reasons && recommendation.reasons.length
                ? recommendation.reasons[0]
                : task.due_label;
            return listItem(task.title, reason);
          })
          .join("");
        return (
          '<article class="task-card"><h3>' +
          escapeHtml(group.label) +
          '</h3><p class="muted">' +
          escapeHtml(group.detail) +
          '</p><div class="compact-list">' +
          items +
          "</div></article>"
        );
      })
      .join("");
  }

  function renderDecisionChips(decision) {
    var chips = [
      '<span class="chip warm">' + escapeHtml(decision.status || "inbox") + "</span>",
      '<span class="chip">' + escapeHtml(decision.owner_label || "Unassigned") + "</span>",
      '<span class="chip green">' + escapeHtml(decision.due_label || "No due date") + "</span>",
    ];
    if (decision.urgency === "critical" || decision.urgency === "high") {
      chips.push('<span class="chip alert">' + escapeHtml(decision.urgency) + "</span>");
    }
    return chips.join("");
  }

  function renderDecisions(data) {
    data = data || {};
    var open = data.open || [];
    var attention = data.attention || [];
    setText("decision-count-label", open.length + " open");
    setText("decision-attention-count", attention.length);
    setCardHidden("decision-attention", !attention.length);

    var attentionNode = byId("decision-attention");
    if (attentionNode) {
      attentionNode.innerHTML = attention.length
        ? attention
            .map(function (decision) {
              var missing =
                decision.missing_fields && decision.missing_fields.length
                  ? "Missing " + decision.missing_fields.join(", ")
                  : decision.due_label;
              return listItem(decision.title, missing);
            })
            .join("")
        : "";
    }

    var node = byId("decision-items");
    if (!node) return;
    setContainerHidden("decision-items", !open.length);
    if (!open.length) {
      node.innerHTML = "";
      return;
    }
    node.innerHTML = open
      .map(function (decision) {
        var missing = decision.missing_fields || [];
        var missingHtml = missing.length
          ? '<p class="decision-missing">Missing: ' + escapeHtml(missing.join(", ")) + "</p>"
          : '<p class="decision-ready">Ready for family discussion</p>';
        return (
          '<article class="decision-card"><div class="decision-card-top"><p class="eyebrow">' +
          escapeHtml(decision.short_id || "decision") +
          "</p>" +
          renderDecisionChips(decision) +
          "</div><h3>" +
          escapeHtml(decision.title) +
          '</h3><div class="decision-stats"><span>' +
          escapeHtml((decision.option_count || 0) + " options") +
          "</span><span>" +
          escapeHtml((decision.evidence_count || 0) + " notes") +
          "</span></div>" +
          missingHtml +
          '<div class="decision-next"><strong>Next step</strong><span>' +
          escapeHtml(decision.next_step || "Assign one clear next step") +
          '</span></div><div class="decision-actions">' +
          decisionCompleteButton(decision) +
          "</div></article>"
        );
      })
      .join("");
  }

  function backlogNeedsAttention(item) {
    return !!(
      item.pinned ||
      item.blocked ||
      item.stale ||
      (item.days_until !== null && item.days_until <= 0)
    );
  }

  function backlogFilteredItems(items) {
    var ownerNode = byId("backlog-owner-filter");
    var stateNode = byId("backlog-state-filter");
    var owner = ownerNode ? ownerNode.value : "all";
    var state = stateNode ? stateNode.value : "all";
    return (items || []).filter(function (item) {
      if (owner !== "all" && item.owner !== owner) return false;
      if (state === "attention" && !backlogNeedsAttention(item)) return false;
      if (state === "pinned" && !item.pinned) return false;
      return true;
    });
  }

  function backlogActionButton(item, action, label) {
    return (
      '<button type="button" class="backlog-action" data-backlog-action="' +
      escapeHtml(action) +
      '" data-backlog-id="' +
      escapeHtml(item.id) +
      '">' +
      escapeHtml(label) +
      "</button>"
    );
  }

  function backlogCard(item) {
    var flags = [];
    if (item.pinned) flags.push('<span class="chip warm">Pinned</span>');
    if (item.blocked) flags.push('<span class="chip alert">Blocked</span>');
    if (item.stale) flags.push('<span class="chip alert">Stale</span>');
    if (item.ready_to_close) flags.push('<span class="chip green">Ready to close</span>');
    var reviewIds = (lastBacklogData.review || {}).item_ids || [];
    var reviewCurrent = backlogReviewIndex >= 0 && reviewIds[backlogReviewIndex] === item.id;
    var positions = (item.positions || [])
      .map(function (position) {
        return (
          "<span>" + escapeHtml(position.actor) + ": " + escapeHtml(position.value) + "</span>"
        );
      })
      .join("");
    var links = (item.links || [])
      .map(function (link) {
        return (
          '<span class="backlog-link ' +
          (link.available ? "" : "is-missing") +
          '">' +
          escapeHtml(link.title) +
          (link.completed ? " (done)" : link.available ? "" : " (missing)") +
          "</span>"
        );
      })
      .join("");
    var nextKinds = ["discussion", "planning", "decision"].filter(function (kind) {
      return kind !== item.kind;
    });
    return (
      '<article class="backlog-item' +
      (reviewCurrent ? " is-review-current" : "") +
      '">' +
      '<div class="backlog-item-top"><span class="backlog-ref">' +
      escapeHtml(item.short_id) +
      '</span><div class="chip-row">' +
      flags.join("") +
      "</div></div>" +
      "<h4>" +
      escapeHtml(item.title) +
      "</h4>" +
      '<p class="backlog-meta">' +
      escapeHtml(item.owner_label) +
      " | " +
      escapeHtml(item.date_label) +
      "</p>" +
      (item.context ? '<p class="backlog-context">' + escapeHtml(item.context) + "</p>" : "") +
      (positions ? '<div class="backlog-positions">' + positions + "</div>" : "") +
      (links ? '<div class="backlog-links">' + links + "</div>" : "") +
      "<details><summary>Details and actions</summary>" +
      '<div class="backlog-action-row">' +
      backlogActionButton(item, "note", "Add note") +
      (item.kind === "discussion" ? backlogActionButton(item, "position", "Set position") : "") +
      backlogActionButton(item, "edit", "Edit") +
      backlogActionButton(item, "pin", item.pinned ? "Unpin" : "Pin") +
      '</div><div class="backlog-action-row"><select data-backlog-move-kind="' +
      escapeHtml(item.id) +
      '" aria-label="Move item to"><option value="">Move to...</option>' +
      nextKinds
        .map(function (kind) {
          return (
            '<option value="' +
            kind +
            '">' +
            kind.charAt(0).toUpperCase() +
            kind.slice(1) +
            "</option>"
          );
        })
        .join("") +
      "</select>" +
      backlogActionButton(item, "move", "Move") +
      backlogActionButton(item, "link_event", "Link event") +
      backlogActionButton(item, "link_task", "Link task") +
      (item.kind === "decision" ? backlogActionButton(item, "create_task", "Create task") : "") +
      backlogActionButton(item, "park", "Park") +
      backlogActionButton(item, "close", "Close") +
      (reviewCurrent ? backlogActionButton(item, "keep", "Keep") : "") +
      "</div></details></article>"
    );
  }

  function renderBacklogLane(kind, targetId) {
    var lane = (lastBacklogData.lanes || {})[kind] || [];
    var items = backlogFilteredItems(lane);
    setText(kind + "-count", lane.length);
    var node = byId(targetId);
    if (node)
      node.innerHTML = items.length ? items.map(backlogCard).join("") : empty("No matching items");
  }

  function renderBacklog(data) {
    lastBacklogData = data || {};
    var attention = lastBacklogData.attention || [];
    setText("backlog-attention-count", attention.length);
    setCardHidden("backlog-attention", !attention.length);
    var attentionNode = byId("backlog-attention");
    if (attentionNode) {
      attentionNode.innerHTML = attention
        .map(function (item) {
          return listItem(item.title, item.date_label + " | " + item.kind);
        })
        .join("");
    }
    var reviewButton = byId("backlog-review-button");
    if (reviewButton)
      reviewButton.classList.toggle("is-called-out", !!(lastBacklogData.review || {}).callout);
    renderBacklogLane("discussion", "discussion-items");
    renderBacklogLane("planning", "planning-items");
    renderBacklogLane("decision", "decision-backlog-items");
  }

  function setBacklogStatus(message, state) {
    var node = byId("backlog-action-status");
    if (!node) return;
    node.textContent = message || "";
    node.dataset.state = state || "";
  }

  function postBacklog(path, payload, done) {
    var request = new XMLHttpRequest();
    request.open("POST", path, true);
    request.setRequestHeader("Content-Type", "application/json");
    request.setRequestHeader("X-N4OS-Dashboard-Action-Token", actionToken());
    request.onreadystatechange = function () {
      if (request.readyState !== 4) return;
      var response = {};
      try {
        response = JSON.parse(request.responseText || "{}");
      } catch (_error) {
        response = {};
      }
      if (request.status >= 200 && request.status < 300 && response.status === "ok") {
        setBacklogStatus(response.message || "Backlog updated.", "ok");
        if (done) done(response);
        loadDashboard();
      } else {
        setBacklogStatus(response.message || "Backlog update failed.", "error");
      }
    };
    request.send(JSON.stringify(payload));
  }

  function handleBacklogAction(target) {
    var action = target.getAttribute("data-backlog-action");
    var itemId = target.getAttribute("data-backlog-id");
    var payload = { action: action, item_id: itemId };
    if (action === "keep") {
      var reviewIds = (lastBacklogData.review || {}).item_ids || [];
      backlogReviewIndex = Math.min(backlogReviewIndex + 1, reviewIds.length - 1);
      if (backlogReviewIndex >= reviewIds.length - 1)
        setBacklogStatus("Weekly review complete.", "ok");
      renderBacklog(lastBacklogData);
      return;
    }
    if (action === "note") {
      payload.action = "add_note";
      payload.text = window.prompt("Note");
      if (!payload.text) return;
    } else if (action === "position") {
      payload.action = "set_position";
      payload.value = window.prompt("Position: yes, no, or unsure", "unsure");
      if (!payload.value) return;
    } else if (action === "edit") {
      payload.title = window.prompt("Title");
      if (!payload.title) return;
    } else if (action === "pin") {
      var item = ["discussion", "planning", "decision"].reduce(function (found, kind) {
        return (
          found ||
          ((lastBacklogData.lanes || {})[kind] || []).find(function (row) {
            return row.id === itemId;
          })
        );
      }, null);
      payload.pinned = !(item && item.pinned);
    } else if (action === "move") {
      var select = document.querySelector('[data-backlog-move-kind="' + itemId + '"]');
      payload.kind = select ? select.value : "";
      if (!payload.kind || !window.confirm("Move this item to " + payload.kind + "?")) return;
      payload.confirmed = true;
    } else if (action === "link_event" || action === "link_task") {
      payload.external_id = window.prompt(
        action === "link_event" ? "Calendar event ID" : "Google Task ID",
      );
      if (!payload.external_id) return;
      if (action === "link_task") payload.container_id = "@default";
    } else if (action === "create_task") {
      var backlogItem = ["discussion", "planning", "decision"].reduce(function (found, kind) {
        return (
          found ||
          ((lastBacklogData.lanes || {})[kind] || []).find(function (row) {
            return row.id === itemId;
          })
        );
      }, null);
      var defaultTitle =
        backlogItem && backlogItem.next_step && backlogItem.next_step !== "Assign one clear next step"
          ? backlogItem.next_step
          : "";
      payload.title = window.prompt("Follow-up task", defaultTitle);
      if (!payload.title) return;
      payload.notes = backlogItem ? "Follow-up for decision: " + backlogItem.title : "";
      payload.due = backlogItem ? backlogItem.next_step_due || backlogItem.due || "" : "";
      payload.container_id = "@default";
    } else if (action === "close") {
      payload.outcome = window.prompt("Recorded outcome", "Closed from dashboard.");
      if (payload.outcome === null || !window.confirm("Close this backlog item?")) return;
      payload.confirmed = true;
    }
    postBacklog("/api/backlog/actions", payload);
  }

  function renderFamily(data) {
    data = data || {};
    var members = data.members || [];
    setText("family-count", members.length + " known");
    setText("unassigned-count", data.unassigned ? data.unassigned.length : 0);
    var memberNode = byId("family-members");
    setCardHidden("family-members", !members.length);
    if (memberNode) {
      memberNode.innerHTML = members.length
        ? members
            .map(function (member) {
              return (
                '<div class="member"><div class="member-avatar">' +
                escapeHtml(member.name.slice(0, 1)) +
                "</div><div><strong>" +
                escapeHtml(member.name) +
                '</strong><p class="muted">' +
                escapeHtml(member.responsibility_count + " responsibilities today") +
                "</p></div></div>"
              );
            })
            .join("")
        : "";
    }
    var responsibilities = byId("responsibilities");
    var responsibilityItems = data.responsibilities || [];
    setCardHidden("responsibilities", !responsibilityItems.length);
    if (responsibilities) {
      responsibilities.innerHTML = responsibilityItems.length
        ? responsibilityItems
            .map(function (item) {
              return listItem(item.title, item.owner + " | " + item.detail);
            })
            .join("")
        : "";
    }
    var childEvents = byId("child-events");
    var childEventItems = data.child_events || [];
    setCardHidden("child-events", !childEventItems.length);
    if (childEvents) {
      childEvents.innerHTML = childEventItems.length
        ? childEventItems
            .map(function (event) {
              return listItem(
                event.title,
                [event.person, event.time_label].filter(Boolean).join(" | "),
              );
            })
            .join("")
        : "";
    }
    var unassigned = byId("unassigned");
    var unassignedItems = data.unassigned || [];
    setCardHidden("unassigned", !unassignedItems.length);
    if (unassigned) {
      unassigned.innerHTML = unassignedItems.length
        ? unassignedItems
            .map(function (item) {
              return listItem(item.title, item.detail);
            })
            .join("")
        : "";
    }
  }

  function render(data) {
    renderSummary(data);
    setText("greeting", text(data.greeting, "Hello"));
    setText("date-label", data.date_label);
    setText("source-status", data.source_message || data.source_status);
    setText(
      "updated-at",
      "Updated " +
        new Date(data.generated_at).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }),
    );

    var action = data.best_next_action || {};
    setText("best-action-title", action.title);
    setText("best-action-why", action.why);
    renderReasons(action.reasons || []);

    var calendar = data.calendar || {};
    setText("busy-day-label", calendar.busy_day ? calendar.busy_day.label : "");
    renderTimeline(calendar.today || []);
    renderReadingGarden(data.reading_garden || {});
    renderHomeBoard(data.home_board || {});
    renderBedtime(data.bedtime || {});
    renderShopping(data.shopping || {});
    renderPrep(calendar.prep_needed || []);
    renderWarnings(data.warnings || []);

    var tasks = data.tasks || {};
    renderBacklog(data.backlog || {});
    lastTasksData = tasks;
    renderTasks(tasks);
    renderFamily(data.family || {});
    updateActiveNav();
    scrollToHashSection();
  }

  function renderTasks(tasks) {
    tasks = tasks || {};
    var pendingTasks = tasks.pending || [];
    var selectedTag = selectedTaskTag();
    var selectedOwner = selectedTaskOwner();
    var selectedDue = selectedTaskDueFilter();
    var ownerFilteredTasks = pendingTasks.filter(function (task) {
      return taskMatchesOwner(task, selectedOwner) && taskMatchesDueFilter(task, selectedDue);
    });
    var availableTags = tasks.tags || [];
    if (selectedOwner !== "all" || selectedDue) {
      availableTags = Array.from(
        new Set(
          ownerFilteredTasks.reduce(function (tags, task) {
            return tags.concat(task.tags || []);
          }, []),
        ),
      ).sort();
    }
    var filteredTasks = filterTasks(pendingTasks, selectedTag, selectedOwner, selectedDue);
    renderTaskOwnerFilter(tasks.owners || [], selectedOwner);
    renderTaskTagFilters(availableTags, selectedTag, filteredTasks.length);
    renderPendingTasks(filteredTasks, pendingTasks.length, selectedDue);
    renderTaskGroups(tasks.groups || [], selectedTag);
  }

  function loadDashboard() {
    var request = new XMLHttpRequest();
    request.open("GET", "/api/dashboard", true);
    request.onreadystatechange = function () {
      if (request.readyState !== 4) return;
      if (request.status >= 200 && request.status < 300) {
        render(JSON.parse(request.responseText));
      } else {
        setText("source-status", "Dashboard API unavailable");
      }
    };
    request.send();
  }

  loadBedtimeVoicePreference();
  loadDashboard();
  syncWakeLock();
  window.setInterval(loadDashboard, refreshMs);
  window.setInterval(syncWakeLock, wakeLockCheckMs);
  document.addEventListener("visibilitychange", syncWakeLock);
  document.addEventListener("click", function (event) {
    var target = closestActionTarget(event.target);
    if (!target) return;
    if (target.getAttribute("data-backlog-action") !== null) {
      handleBacklogAction(target);
      return;
    }
    if (target.getAttribute("data-task-tag-filter") !== null) {
      setSelectedTaskTag(target.getAttribute("data-task-tag-filter"));
      renderTasks(lastTasksData);
      updateActiveNav();
      scrollToHashSection();
      return;
    }
    if (target.getAttribute("data-task-owner-chip") !== null) {
      event.preventDefault();
      setSelectedTaskTag("");
      setSelectedTaskOwner(target.getAttribute("data-task-owner-chip"));
      setSelectedTaskDueFilter(target.getAttribute("data-task-owner-due") || "");
      renderTasks(lastTasksData);
      updateActiveNav();
      scrollToHashSection();
      return;
    }
    if (target.getAttribute("data-reading-child") !== null) {
      selectedReadingChild = target.getAttribute("data-reading-child") || selectedReadingChild;
      renderReadingGarden(lastReadingData);
      return;
    }
    if (target.getAttribute("data-reading-heatmap-day") !== null) {
      showReadingHeatmapDetail(target);
      return;
    }
    if (target.getAttribute("data-reading-update") !== null) {
      postReadingUpdate(target.getAttribute("data-reading-update") || "", target);
      return;
    }
    if (target.getAttribute("data-reading-delete") !== null) {
      postReadingDelete(target.getAttribute("data-reading-delete") || "", target);
      return;
    }
    if (target.getAttribute("data-bedtime-voice") !== null) {
      setBedtimeVoiceEnabled(!bedtimeVoiceEnabled);
      if (bedtimeVoiceEnabled) {
        speakBedtimeNudge("Bedtime voice is on.");
      } else {
        setBedtimeStatus("Bedtime voice off.", "pending");
      }
      return;
    }
    if (target.getAttribute("data-bedtime-ack") !== null) {
      postBedtimeAck(target.getAttribute("data-bedtime-ack") || "", target);
      return;
    }
    if (target.getAttribute("data-decision-complete") !== null) {
      postCompleteDecision(target.getAttribute("data-decision-complete"), target);
      return;
    }
    if (target.getAttribute("data-shopping-tab") !== null) {
      selectedShoppingList = target.getAttribute("data-shopping-tab") || "all";
      renderShopping(lastShoppingData);
      return;
    }
    if (target.getAttribute("data-shopping-check") !== null) {
      postShoppingCheck(
        target.getAttribute("data-shopping-check"),
        target.getAttribute("data-shopping-list"),
        target,
      );
      return;
    }
    if (target.getAttribute("data-shopping-clear") !== null) {
      postShoppingClear(target.getAttribute("data-shopping-clear"), target);
      return;
    }
    postCompleteTask(target.getAttribute("data-task-complete"), target);
  });
  window.addEventListener("focus", syncWakeLock);
  window.addEventListener("hashchange", function () {
    updateActiveNav();
    scrollToHashSection();
  });
  var wakeButton = byId("screen-wake-button");
  if (wakeButton) {
    wakeButton.addEventListener("click", syncWakeLock);
  }
  ["backlog-owner-filter", "backlog-state-filter"].forEach(function (id) {
    var filter = byId(id);
    if (filter)
      filter.addEventListener("change", function () {
        renderBacklog(lastBacklogData);
      });
  });
  var taskOwnerFilter = byId("task-owner-filter");
  if (taskOwnerFilter) {
    taskOwnerFilter.addEventListener("change", function () {
      setSelectedTaskOwner(taskOwnerFilter.value);
      setSelectedTaskDueFilter("");
      renderTasks(lastTasksData);
      updateActiveNav();
      scrollToHashSection();
    });
  }
  var backlogDialog = byId("backlog-add-dialog");
  var backlogAddButton = byId("backlog-add-button");
  var backlogForm = byId("backlog-add-form");
  function closeBacklogDialog() {
    if (backlogDialog && backlogDialog.open) backlogDialog.close();
  }
  if (backlogAddButton && backlogDialog) {
    backlogAddButton.addEventListener("click", function () {
      backlogDialog.showModal();
      var titleInput = byId("backlog-title-input");
      if (titleInput) titleInput.focus();
    });
  }
  ["backlog-dialog-close", "backlog-dialog-cancel"].forEach(function (id) {
    var button = byId(id);
    if (button) button.addEventListener("click", closeBacklogDialog);
  });
  if (backlogForm) {
    backlogForm.addEventListener("change", function (event) {
      if (event.target && event.target.name === "kind") {
        setText("backlog-date-label", event.target.value === "discussion" ? "Review date" : "Date");
      }
    });
    backlogForm.addEventListener("submit", function (event) {
      event.preventDefault();
      var form = new FormData(backlogForm);
      postBacklog(
        "/api/backlog/items",
        {
          kind: form.get("kind"),
          title: form.get("title"),
          owner: form.get("owner"),
          date: form.get("date"),
        },
        function () {
          backlogForm.reset();
          setText("backlog-date-label", "Review date");
          closeBacklogDialog();
        },
      );
    });
  }
  var backlogReviewButton = byId("backlog-review-button");
  if (backlogReviewButton) {
    backlogReviewButton.addEventListener("click", function () {
      var reviewIds = (lastBacklogData.review || {}).item_ids || [];
      backlogReviewIndex = reviewIds.length ? 0 : -1;
      setBacklogStatus(reviewIds.length ? "Weekly review started." : "Backlog is clear.", "ok");
      renderBacklog(lastBacklogData);
    });
  }
})();
