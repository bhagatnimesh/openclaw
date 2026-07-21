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
    node.innerHTML = "<strong>" + count + "</strong> " + escapeHtml(count === 1 ? singular : plural);
  }

  function classToken(value, fallback) {
    return text(value, fallback).toLowerCase().replace(/[^a-z0-9_-]+/g, "-").replace(/^-+|-+$/g, "") || fallback;
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
      if (target.getAttribute("data-task-complete") !== null || target.getAttribute("data-task-tag-filter") !== null) {
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
    return text(value).trim().replace(/^#/, "").toLowerCase().replace(/[^a-z0-9_-]+/g, "-").replace(/^-+|-+$/g, "");
  }

  function selectedTaskTag() {
    return normalizeTag(getQueryParam("tag"));
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
      var label = window.isSecureContext === false ? "Screen wake needs HTTPS" : "Screen wake unsupported";
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
    node.innerHTML = reasons.map(function (reason) {
      return "<span>" + escapeHtml(reason) + "</span>";
    }).join("");
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
    node.innerHTML = events.map(function (event) {
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
    }).join("");
  }

  function renderHomeBoard(items) {
    var node = byId("home-board-items");
    if (!node) return;
    items = items || [];
    setText("home-board-count", items.length + " pending");
    setCardHidden("home-board", !items.length);
    if (!items.length) {
      node.innerHTML = "";
      return;
    }

    var grouped = {};
    items.forEach(function (item) {
      var person = text(item.person_or_group, "Family");
      if (!grouped[person]) grouped[person] = [];
      grouped[person].push(item);
    });

    node.innerHTML = Object.keys(grouped).sort().map(function (person) {
      var rows = grouped[person].map(function (item) {
        var priority = classToken(item.priority, "medium");
        return (
          '<div class="home-board-item priority-' +
          escapeHtml(priority) +
          '"><span class="home-board-check" aria-hidden="true"></span><div><strong>' +
          escapeHtml(item.message) +
          '</strong><span>' +
          escapeHtml(item.context_label || "General") +
          "</span></div></div>"
        );
      }).join("");
      return (
        '<section class="home-board-group"><h4>' +
        escapeHtml(person) +
        '</h4><div class="home-board-group-items">' +
        rows +
        "</div></section>"
      );
    }).join("");
  }

  function gardenToken(className, label) {
    return '<span class="garden-token ' + escapeHtml(className) + '" aria-label="' + escapeHtml(label) + '"></span>';
  }

  function gardenTokens(count, className, label, maxCount) {
    var total = Math.max(0, Math.min(Number(count || 0), maxCount));
    var html = "";
    for (var index = 0; index < total; index += 1) {
      html += gardenToken(className, label);
    }
    return html;
  }

  function readingEventDetail(event) {
    var parts = [];
    if (event.pages) parts.push(event.pages + " pages");
    if (event.minutes) parts.push(event.minutes + " minutes");
    if (event.status === "completed") parts.push("finished");
    if (!parts.length) parts.push("reading moment");
    return parts.join(" | ");
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
    node.innerHTML = books.slice(0, 10).map(function (book, index) {
      var initial = book.title.slice(0, 1).toUpperCase();
      var tone = index % 3 === 0 ? "is-primary" : index % 3 === 1 ? "is-secondary" : "is-tertiary";
      return (
        '<div class="reading-book-tile"><div class="reading-book-mini-cover ' +
        tone +
        '"><span>' +
        escapeHtml(initial) +
        '</span></div><strong>' +
        escapeHtml(book.title) +
        '</strong><span>' +
        escapeHtml(book.status) +
        "</span></div>"
      );
    }).join("");
  }

  function renderReadingGarden(data) {
    data = data || {};
    var today = data.today || {};
    var week = data.week || {};
    var finished = data.finished || {};
    var garden = data.garden || {};
    var recentEvents = data.recent_events || [];
    var currentBook = data.current_book || "unknown book";
    var momentCount = Number(week.reading_moments || 0);
    var pageCount = Number(week.pages || 0);
    var minuteCount = Number(week.minutes || 0);
    setText("reading-garden-title", data.title || "Nysha’s Reading Garden: I Read It Myself");
    setText("reading-today-label", today.label || "Not yet today");
    setText("reading-hero-week", momentCount + (momentCount === 1 ? " moment this week" : " moments this week"));
    setText(
      "reading-hero-message",
      today.read
        ? "Your garden grew today. Every page counts."
        : "A reading moment can grow the garden today."
    );
    setText("reading-current-book", currentBook);
    setText("reading-current-detail", currentBook === "unknown book" ? "Waiting for the next independent read" : "Latest independent reading book");
    setText("reading-week-moments", momentCount);
    setText("reading-week-pages", pageCount);
    setText("reading-week-minutes", minuteCount);
    setText("reading-finished-count", finished.count || 0);
    setText("reading-moment-count", recentEvents.length + (recentEvents.length === 1 ? " moment" : " moments"));
    setText("reading-bloomed-count", finished.count || 0);
    var progress = Math.max(8, Math.min(100, (momentCount * 18) + (pageCount > 0 ? 18 : 0) + (minuteCount > 0 ? 14 : 0)));
    var progressFill = byId("reading-progress-fill");
    if (progressFill) {
      progressFill.style.width = progress + "%";
    }

    var reactionNode = byId("reading-favorite-reaction");
    if (reactionNode) {
      reactionNode.textContent = data.favorite_reaction ? "Favorite discovery: " + data.favorite_reaction : "";
      reactionNode.hidden = !data.favorite_reaction;
    }

    var plot = byId("reading-garden-plot");
    if (plot) {
      var plotHtml = [
        gardenTokens(garden.sprouts, "is-sprout", "reading day sprout", 8),
        gardenTokens(garden.leaves, "is-leaf", "five page leaf", 10),
        gardenTokens(garden.flowers, "is-flower", "finished book flower", 8),
        gardenTokens(garden.butterflies, "is-butterfly", "favorite discovery butterfly", 6),
      ].join("");
      plot.innerHTML = plotHtml || '<span class="garden-token is-soil" aria-label="garden soil"></span>';
    }

    var finishedNode = byId("reading-finished-books");
    var recentBooks = finished.recent_books || [];
    setCardHidden("reading-bloomed", !recentBooks.length);
    if (finishedNode) {
      finishedNode.innerHTML = recentBooks.length
        ? recentBooks.slice(0, 4).map(function (book) {
            return (
              '<div class="reading-bloomed-item"><span class="garden-token is-flower" aria-hidden="true"></span><div><strong>' +
              escapeHtml(book) +
              '</strong><span>I read it myself</span></div></div>'
            );
          }).join("")
        : "";
    }

    var photoNode = byId("reading-photo-list");
    var photos = data.recent_photos || [];
    setText("reading-photo-count", photos.length);
    setCardHidden("reading-photos", !photos.length);
    if (photoNode) {
      photoNode.innerHTML = photos.length
        ? photos.slice(0, 6).map(function (photo) {
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
          }).join("")
        : "";
    }

    var eventsNode = byId("reading-recent-events");
    setCardHidden("reading-moments", !recentEvents.length);
    if (eventsNode) {
      eventsNode.innerHTML = recentEvents.length
        ? recentEvents.slice(0, 6).map(function (event) {
            var title = event.book && event.book !== "unknown book" ? event.book : "Reading moment";
            return (
              '<div class="reading-event-item"><span class="garden-token is-sprout" aria-hidden="true"></span><div><strong>' +
              escapeHtml(title) +
              '</strong><span>' +
              escapeHtml(readingEventDetail(event)) +
              "</span></div></div>"
            );
          }).join("")
        : "";
    }

    renderLibraryBag(data.library_visit || {}, data.current_bag || {});
    renderBookCollection(data);
  }

  function renderLibraryBag(visit, bag) {
    visit = visit || {};
    bag = bag || {};
    var count = Number(bag.count || 0);
    var days = visit.days_since_visit;
    var daysLabel = visit.has_visit
      ? (days === 0 ? "Today" : days + (days === 1 ? " day ago" : " days ago"))
      : "Not started";
    setText("library-visit-days", daysLabel);
    setText("library-visit-label", visit.label || "Paste a library checkout email to start your library bag.");
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
      ? titles.slice(0, 12).map(function (title) {
          return (
            '<div class="library-bag-item"><span class="garden-token is-leaf" aria-hidden="true"></span><strong>' +
            escapeHtml(title) +
            "</strong></div>"
          );
        }).join("")
      : "";
  }

  function renderSummary(data) {
    var summary = data.summary || {};
    var family = data.family || {};
    setSummaryChip("summary-open", summary.open_loop_count, "task", "tasks");
    setSummaryChip("summary-prep", summary.prep_needed_count, "prep", "prep");
    setSummaryChip("summary-decisions", summary.open_decision_count, "decision", "decisions");
    setSummaryChip("summary-home", summary.home_board_count, "home", "home");
    setSummaryChip(
      "summary-family",
      (family.responsibilities || []).length + (family.child_events || []).length + (family.unassigned || []).length,
      "family",
      "family"
    );
  }

  function renderOpenLoops(tasks) {
    var node = byId("open-loops");
    if (!node) return;
    tasks = tasks || [];
    setCardHidden("open-loops", !tasks.length);
    if (!tasks.length) {
      node.innerHTML = "";
      return;
    }
    node.innerHTML = tasks.slice(0, 3).map(function (task) {
      return listItem(task.title, [task.due_label, task.owner_label].join(" | "));
    }).join("");
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
    node.innerHTML = events.slice(0, 3).map(function (event) {
      return listItem(event.title, event.preparation_notes || event.day_label);
    }).join("");
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
    node.innerHTML = warnings.slice(0, 3).map(function (warning) {
      return listItem(warning.title, warning.detail);
    }).join("");
  }

  function renderPlanning(items) {
    var node = byId("planning-items");
    if (!node) return;
    items = items || [];
    setContainerHidden("planning-items", !items.length);
    if (!items.length) {
      node.innerHTML = "";
      return;
    }
    node.innerHTML = items.map(function (item) {
      var progress = item.prep_progress;
      var progressHtml = "";
      if (progress !== null && progress !== undefined) {
        progressHtml =
          '<div class="progress" aria-label="Preparation progress"><div style="width:' +
          Number(progress) +
          '%"></div></div>';
      }
      var actions = item.action_items && item.action_items.length
        ? '<div class="mini-list">' + item.action_items.map(function (task) {
            return listItem(task.title, task.due_label || task.owner_label);
          }).join("") + "</div>"
        : empty("No linked action items yet.");
      return (
        '<article class="planning-card"><p class="eyebrow">' +
        escapeHtml(item.days_until === 0 ? "Today" : "In " + item.days_until + " days") +
        "</p><h3>" +
        escapeHtml(item.title) +
        '</h3><div class="meta-row"><span class="chip warm">' +
        escapeHtml(item.category || "planning") +
        '</span><span class="chip">' +
        escapeHtml(item.owner_label) +
        '</span><span class="chip green">' +
        escapeHtml(item.date_label) +
        "</span>" +
        (item.prep_needed ? '<span class="chip alert">Prep needed</span>' : "") +
        "</div>" +
        progressHtml +
        '<p class="muted">' +
        escapeHtml(item.prep_notes || "No prep notes available.") +
        "</p>" +
        actions +
        "</article>"
      );
    }).join("");
  }

  function taskBadge(label, tone) {
    if (!label) return "";
    return '<span class="task-badge ' + escapeHtml(tone || "") + '">' + escapeHtml(label) + "</span>";
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
    var tagButtons = tags.map(function (tag) {
      return (
        '<button class="task-tag-filter ' +
        (tag === selectedTag ? "is-active" : "") +
        '" type="button" data-task-tag-filter="' +
        escapeHtml(tag) +
        '">#' +
        escapeHtml(tag) +
        "</button>"
      );
    }).join("");
    var resultLabel = selectedTag ? '<span class="task-tag-result">' + escapeHtml(taskCount) + " shown</span>" : "";
    node.innerHTML = allButton + tagButtons + resultLabel;
  }

  function taskTone(task) {
    if (task.days_until_due !== null && task.days_until_due !== undefined && task.days_until_due < 0) {
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

  function actionToken() {
    var node = document.querySelector('meta[name="n4os-dashboard-action-token"]');
    return node ? node.getAttribute("content") || "" : "";
  }

  function taskCompleteButton(task) {
    if (!task.id) return "";
    return (
      '<button class="task-complete-button" type="button" data-task-complete="' +
      escapeHtml(task.id) +
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

  function renderPendingTasks(tasks, allTaskCount) {
    var node = byId("pending-task-items");
    tasks = tasks || [];
    allTaskCount = allTaskCount === undefined ? tasks.length : allTaskCount;
    var dueCount = tasks.filter(function (task) {
      return task.days_until_due !== null && task.days_until_due !== undefined && task.days_until_due <= 0;
    }).length;
    var unassignedCount = tasks.filter(function (task) {
      return task.owner === "unknown";
    }).length;
    var unscheduledCount = tasks.filter(function (task) {
      return task.days_until_due === null || task.days_until_due === undefined;
    }).length;
    var countLabel = tasks.length === allTaskCount ? tasks.length + " pending" : tasks.length + " of " + allTaskCount + " pending";
    setText("task-count-label", countLabel);
    setText("pending-task-count", countLabel);
    setText("task-due-count", dueCount);
    setText("task-unassigned-count", unassignedCount);
    setText("task-unscheduled-count", unscheduledCount);
    setCardHidden("pending-tasks", !allTaskCount);
    setCardHidden("task-triage", dueCount + unassignedCount + unscheduledCount === 0);
    if (!node) return;
    if (!tasks.length) {
      node.innerHTML = empty("No pending tasks match this tag.");
      return;
    }
    node.innerHTML = tasks.slice(0, 10).map(function (task) {
      var context = taskContextLabel(task);
      var badges = [
        taskBadge(task.due_label || "No due date", task.days_until_due !== null && task.days_until_due !== undefined && task.days_until_due <= 0 ? "alert" : "warm"),
        taskBadge(task.owner_label || "Unassigned", task.owner === "unknown" ? "alert" : "green"),
        task.duration_minutes ? taskBadge(task.duration_minutes + " min", "") : "",
        context ? taskBadge(context, "") : "",
      ].concat((task.tags || []).map(taskTagBadge)).join("");
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
    }).join("");
  }

  function renderTaskGroups(groups, selectedTag) {
    var node = byId("task-groups");
    if (!node) return;
    var visibleGroups = (groups || []).map(function (group) {
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
    }).filter(function (group) {
      return group.items && group.items.length;
    });
    setContainerHidden("task-lanes-heading", !visibleGroups.length);
    setContainerHidden("task-groups", !visibleGroups.length);
    if (!visibleGroups.length) {
      node.innerHTML = "";
      return;
    }
    node.innerHTML = visibleGroups.map(function (group) {
      var items = group.items.map(function (recommendation) {
        var task = recommendation.task;
        var reason = recommendation.reasons && recommendation.reasons.length ? recommendation.reasons[0] : task.due_label;
        return listItem(task.title, reason);
      }).join("");
      return (
        '<article class="task-card"><h3>' +
        escapeHtml(group.label) +
        '</h3><p class="muted">' +
        escapeHtml(group.detail) +
        '</p><div class="compact-list">' +
        items +
        "</div></article>"
      );
    }).join("");
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
        ? attention.map(function (decision) {
            var missing = decision.missing_fields && decision.missing_fields.length
              ? "Missing " + decision.missing_fields.join(", ")
              : decision.due_label;
            return listItem(decision.title, missing);
          }).join("")
        : "";
    }

    var node = byId("decision-items");
    if (!node) return;
    setContainerHidden("decision-items", !open.length);
    if (!open.length) {
      node.innerHTML = "";
      return;
    }
    node.innerHTML = open.map(function (decision) {
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
        '</span><span>' +
        escapeHtml((decision.evidence_count || 0) + " notes") +
        "</span></div>" +
        missingHtml +
        '<div class="decision-next"><strong>Next step</strong><span>' +
        escapeHtml(decision.next_step || "Assign one clear next step") +
        "</span></div></article>"
      );
    }).join("");
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
        ? members.map(function (member) {
            return (
              '<div class="member"><div class="member-avatar">' +
              escapeHtml(member.name.slice(0, 1)) +
              '</div><div><strong>' +
              escapeHtml(member.name) +
              '</strong><p class="muted">' +
              escapeHtml(member.responsibility_count + " responsibilities today") +
              "</p></div></div>"
            );
          }).join("")
        : "";
    }
    var responsibilities = byId("responsibilities");
    var responsibilityItems = data.responsibilities || [];
    setCardHidden("responsibilities", !responsibilityItems.length);
    if (responsibilities) {
      responsibilities.innerHTML = responsibilityItems.length
        ? responsibilityItems.map(function (item) {
            return listItem(item.title, item.owner + " | " + item.detail);
          }).join("")
        : "";
    }
    var childEvents = byId("child-events");
    var childEventItems = data.child_events || [];
    setCardHidden("child-events", !childEventItems.length);
    if (childEvents) {
      childEvents.innerHTML = childEventItems.length
        ? childEventItems.map(function (event) {
            return listItem(event.title, [event.person, event.time_label].filter(Boolean).join(" | "));
          }).join("")
        : "";
    }
    var unassigned = byId("unassigned");
    var unassignedItems = data.unassigned || [];
    setCardHidden("unassigned", !unassignedItems.length);
    if (unassigned) {
      unassigned.innerHTML = unassignedItems.length
        ? unassignedItems.map(function (item) {
            return listItem(item.title, item.detail);
          }).join("")
        : "";
    }
  }

  function render(data) {
    renderSummary(data);
    setText("greeting", text(data.greeting, "Hello"));
    setText("date-label", data.date_label);
    setText("source-status", data.source_message || data.source_status);
    setText("updated-at", "Updated " + new Date(data.generated_at).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }));

    var action = data.best_next_action || {};
    setText("best-action-title", action.title);
    setText("best-action-why", action.why);
    renderReasons(action.reasons || []);

    var calendar = data.calendar || {};
    setText("busy-day-label", calendar.busy_day ? calendar.busy_day.label : "");
    renderTimeline(calendar.today || []);
    renderReadingGarden(data.reading_garden || {});
    renderHomeBoard(data.home_board ? data.home_board.today : []);
    renderPrep(calendar.prep_needed || []);
    renderWarnings(data.warnings || []);

    var tasks = data.tasks || {};
    renderOpenLoops(tasks.open_loops || []);
    renderPlanning(data.planning ? data.planning.items : []);
    renderDecisions(data.decisions || {});
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
    var filteredTasks = filterTasksByTag(pendingTasks, selectedTag);
    renderTaskTagFilters(tasks.tags || [], selectedTag, filteredTasks.length);
    renderPendingTasks(filteredTasks, pendingTasks.length);
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

  loadDashboard();
  syncWakeLock();
  window.setInterval(loadDashboard, refreshMs);
  window.setInterval(syncWakeLock, wakeLockCheckMs);
  document.addEventListener("visibilitychange", syncWakeLock);
  document.addEventListener("click", function (event) {
    var target = closestActionTarget(event.target);
    if (!target) return;
    if (target.getAttribute("data-task-tag-filter") !== null) {
      setSelectedTaskTag(target.getAttribute("data-task-tag-filter"));
      renderTasks(lastTasksData);
      updateActiveNav();
      scrollToHashSection();
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
})();
