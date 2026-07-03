(function () {
  var refreshMs = 5 * 60 * 1000;

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

  function empty(label) {
    return '<p class="empty">' + escapeHtml(label) + "</p>";
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
    if (!events || !events.length) {
      node.innerHTML = empty("No calendar events today.");
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
    if (!items.length) {
      node.innerHTML = empty("Nothing pinned for the household today.");
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
        return (
          '<div class="home-board-item priority-' +
          escapeHtml(item.priority || "medium") +
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

  function renderOpenLoops(tasks) {
    var node = byId("open-loops");
    if (!node) return;
    if (!tasks || !tasks.length) {
      node.innerHTML = empty("No urgent task loops.");
      return;
    }
    node.innerHTML = tasks.slice(0, 3).map(function (task) {
      return listItem(task.title, [task.due_label, task.owner_label].join(" | "));
    }).join("");
  }

  function renderPrep(events) {
    var node = byId("prep-needed");
    if (!node) return;
    if (!events || !events.length) {
      node.innerHTML = empty("No prep-needed items in the next 7 days.");
      return;
    }
    node.innerHTML = events.slice(0, 3).map(function (event) {
      return listItem(event.title, event.preparation_notes || event.day_label);
    }).join("");
  }

  function renderWarnings(warnings) {
    var node = byId("warnings");
    if (!node) return;
    if (!warnings || !warnings.length) {
      node.innerHTML = empty("No conflicts or overload warnings.");
      return;
    }
    node.innerHTML = warnings.slice(0, 3).map(function (warning) {
      return listItem(warning.title, warning.detail);
    }).join("");
  }

  function renderPlanning(items) {
    var node = byId("planning-items");
    if (!node) return;
    if (!items || !items.length) {
      node.innerHTML = empty("No upcoming trips, school events, paperwork, birthdays, or medical planning items found.");
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

  function renderTaskGroups(groups) {
    var node = byId("task-groups");
    if (!node) return;
    if (!groups || !groups.length) {
      node.innerHTML = empty("No recommended task groups.");
      return;
    }
    node.innerHTML = groups.map(function (group) {
      var items = group.items && group.items.length
        ? group.items.map(function (recommendation) {
            var task = recommendation.task;
            var reason = recommendation.reasons && recommendation.reasons.length ? recommendation.reasons[0] : task.due_label;
            return listItem(task.title, reason);
          }).join("")
        : empty("Nothing matching this context.");
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

  function renderFamily(data) {
    data = data || {};
    var members = data.members || [];
    setText("family-count", members.length + " known");
    setText("unassigned-count", data.unassigned ? data.unassigned.length : 0);
    var memberNode = byId("family-members");
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
        : empty("No family metadata found in today's events or tasks.");
    }
    var responsibilities = byId("responsibilities");
    if (responsibilities) {
      responsibilities.innerHTML = data.responsibilities && data.responsibilities.length
        ? data.responsibilities.map(function (item) {
            return listItem(item.title, item.owner + " | " + item.detail);
          }).join("")
        : empty("No owned responsibilities today.");
    }
    var childEvents = byId("child-events");
    if (childEvents) {
      childEvents.innerHTML = data.child_events && data.child_events.length
        ? data.child_events.map(function (event) {
            return listItem(event.title, [event.person, event.time_label].filter(Boolean).join(" | "));
          }).join("")
        : empty("No child-specific events today.");
    }
    var unassigned = byId("unassigned");
    if (unassigned) {
      unassigned.innerHTML = data.unassigned && data.unassigned.length
        ? data.unassigned.map(function (item) {
            return listItem(item.title, item.detail);
          }).join("")
        : empty("Nothing unassigned.");
    }
  }

  function render(data) {
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
    renderHomeBoard(data.home_board ? data.home_board.today : []);
    renderPrep(calendar.prep_needed || []);
    renderWarnings(data.warnings || []);

    var tasks = data.tasks || {};
    renderOpenLoops(tasks.open_loops || []);
    renderPlanning(data.planning ? data.planning.items : []);
    renderTaskGroups(tasks.groups || []);
    renderFamily(data.family || {});
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
  window.setInterval(loadDashboard, refreshMs);
})();
