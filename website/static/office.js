(function () {
  var quickReplyText = {
    brief:
      "Спасибо за обращение. Чтобы точнее оценить задачу, пришлите текущий сайт/материалы, цель проекта и желаемый результат.",
    call:
      "Предлагаю начать с короткой диагностики: разберем цель, текущие процессы, интеграции и приоритет запуска.",
    proposal:
      "Я взял задачу в разбор. Следующим сообщением подготовлю структуру работ и предварительную вилку стоимости.",
    invoice: "Счет подготовлен. После согласования суммы можно перейти к оплате и запуску работ."
  };

  function copyText(value) {
    var fallback = function () {
      var input = document.createElement("textarea");
      input.value = value;
      input.setAttribute("readonly", "");
      input.style.position = "fixed";
      input.style.left = "-9999px";
      document.body.appendChild(input);
      input.select();
      document.execCommand("copy");
      input.remove();
    };

    if (navigator.clipboard && navigator.clipboard.writeText) {
      return navigator.clipboard.writeText(value).catch(fallback);
    }
    fallback();
    return Promise.resolve();
  }

  document.querySelectorAll("select[name='quick_reply']").forEach(function (select) {
    select.addEventListener("change", function () {
      var form = select.closest("form");
      if (!form) return;
      var textarea = form.querySelector("textarea[name='body']");
      if (textarea && select.value && !textarea.value.trim()) {
        textarea.value = quickReplyText[select.value] || "";
      }
    });
  });

  document.querySelectorAll("[data-copy]").forEach(function (button) {
    button.addEventListener("click", function () {
      var value = button.getAttribute("data-copy") || "";
      if (!value) return;
      copyText(value).then(function () {
        button.textContent = "Скопировано";
      });
    });
  });

  var pendingConfirm = null;
  var confirmDialog = null;

  function ensureConfirmDialog() {
    if (confirmDialog) return confirmDialog;

    var root = document.createElement("div");
    root.className = "office-confirm";
    root.hidden = true;
    root.innerHTML =
      '<div class="office-confirm__backdrop" data-confirm-cancel></div>' +
      '<section class="office-confirm__dialog" role="dialog" aria-modal="true" aria-labelledby="office-confirm-title" aria-describedby="office-confirm-message">' +
      '<span class="office-confirm__eyebrow">Подтверждение</span>' +
      '<h2 id="office-confirm-title">Подтвердите действие</h2>' +
      '<p id="office-confirm-message"></p>' +
      '<div class="office-confirm__actions">' +
      '<button class="office-confirm__button office-confirm__button--secondary" type="button" data-confirm-cancel>Отмена</button>' +
      '<button class="office-confirm__button office-confirm__button--primary" type="button" data-confirm-accept>Подтвердить</button>' +
      "</div>" +
      "</section>";
    document.body.appendChild(root);

    root.querySelectorAll("[data-confirm-cancel]").forEach(function (button) {
      button.addEventListener("click", closeConfirmDialog);
    });
    root.querySelector("[data-confirm-accept]").addEventListener("click", acceptConfirmDialog);

    confirmDialog = root;
    return confirmDialog;
  }

  function openConfirmDialog(trigger, message) {
    var dialog = ensureConfirmDialog();
    pendingConfirm = { trigger: trigger };
    dialog.querySelector("#office-confirm-message").textContent = message;
    dialog.hidden = false;
    document.body.classList.add("office-confirm-open");
    dialog.querySelector("[data-confirm-accept]").focus();
  }

  function closeConfirmDialog() {
    var trigger = pendingConfirm && pendingConfirm.trigger;
    if (confirmDialog) {
      confirmDialog.hidden = true;
      document.body.classList.remove("office-confirm-open");
    }
    pendingConfirm = null;
    if (trigger && typeof trigger.focus === "function") {
      trigger.focus();
    }
  }

  function acceptConfirmDialog() {
    var trigger = pendingConfirm && pendingConfirm.trigger;
    if (!trigger) {
      closeConfirmDialog();
      return;
    }

    pendingConfirm = null;
    if (confirmDialog) {
      confirmDialog.hidden = true;
      document.body.classList.remove("office-confirm-open");
    }

    var form = trigger.form || trigger.closest("form");
    if (form) {
      trigger.dataset.confirmBypass = "true";
      if (typeof form.requestSubmit === "function") {
        form.requestSubmit(trigger);
      } else {
        form.submit();
      }
      window.setTimeout(function () {
        delete trigger.dataset.confirmBypass;
      }, 0);
      return;
    }

    if (trigger.href) {
      window.location.href = trigger.href;
      return;
    }

    trigger.dataset.confirmBypass = "true";
    trigger.click();
    window.setTimeout(function () {
      delete trigger.dataset.confirmBypass;
    }, 0);
  }

  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && confirmDialog && !confirmDialog.hidden) {
      closeConfirmDialog();
    }
  });

  document.addEventListener("click", function (event) {
    var trigger = event.target.closest("[data-confirm]");
    if (!trigger || trigger.dataset.confirmBypass === "true") return;

    var message = trigger.getAttribute("data-confirm") || "";
    if (!message) return;

    event.preventDefault();
    openConfirmDialog(trigger, message);
  });

  var bulkProjectForm = document.querySelector("[data-project-bulk-form]");
  if (bulkProjectForm) {
    var projectCheckboxes = Array.prototype.slice.call(
      bulkProjectForm.querySelectorAll("[data-project-checkbox]")
    );
    var selectAllProjects = bulkProjectForm.querySelector("[data-project-select-all]");
    var selectedCounter = bulkProjectForm.querySelector("[data-project-selected-count]");
    var modalCounter = bulkProjectForm.querySelector("[data-project-modal-count]");
    var bulkOpenButton = bulkProjectForm.querySelector("[data-project-bulk-open]");
    var bulkModal = bulkProjectForm.querySelector("[data-project-bulk-modal]");

    function selectedProjectsCount() {
      return projectCheckboxes.filter(function (checkbox) {
        return checkbox.checked;
      }).length;
    }

    function syncProjectSelection() {
      var selected = selectedProjectsCount();
      var total = projectCheckboxes.length;
      selectedCounter.textContent = "Выбрано: " + selected;
      modalCounter.textContent = "Выбрано проектов: " + selected;
      bulkOpenButton.disabled = selected === 0;
      selectAllProjects.checked = total > 0 && selected === total;
      selectAllProjects.indeterminate = selected > 0 && selected < total;

      projectCheckboxes.forEach(function (checkbox) {
        var card = checkbox.closest(".office-project-card");
        if (card) card.classList.toggle("office-project-card--selected", checkbox.checked);
      });
    }

    function closeBulkProjectModal() {
      bulkModal.hidden = true;
      document.body.classList.remove("office-confirm-open");
      bulkOpenButton.focus();
    }

    selectAllProjects.addEventListener("change", function () {
      projectCheckboxes.forEach(function (checkbox) {
        checkbox.checked = selectAllProjects.checked;
      });
      syncProjectSelection();
    });

    projectCheckboxes.forEach(function (checkbox) {
      checkbox.addEventListener("change", syncProjectSelection);
    });

    bulkOpenButton.addEventListener("click", function () {
      if (selectedProjectsCount() === 0) return;
      bulkModal.hidden = false;
      document.body.classList.add("office-confirm-open");
      bulkModal.querySelector("input[name='notify_client']:checked").focus();
    });

    bulkModal.querySelectorAll("[data-project-bulk-close]").forEach(function (button) {
      button.addEventListener("click", closeBulkProjectModal);
    });

    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape" && !bulkModal.hidden) {
        closeBulkProjectModal();
      }
    });

    bulkProjectForm.addEventListener("submit", function (event) {
      if (selectedProjectsCount() === 0) {
        event.preventDefault();
        closeBulkProjectModal();
      }
    });

    syncProjectSelection();
  }

  var activeDatePicker = null;
  var dateMonthNames = [
    "Январь",
    "Февраль",
    "Март",
    "Апрель",
    "Май",
    "Июнь",
    "Июль",
    "Август",
    "Сентябрь",
    "Октябрь",
    "Ноябрь",
    "Декабрь"
  ];
  var dateWeekdays = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];

  function padDatePart(value) {
    return String(value).padStart(2, "0");
  }

  function parseIsoDate(value) {
    var parts = String(value || "").split("-");
    if (parts.length !== 3) return null;
    var year = Number(parts[0]);
    var month = Number(parts[1]);
    var day = Number(parts[2]);
    if (!year || !month || !day) return null;
    var date = new Date(year, month - 1, day);
    if (
      date.getFullYear() !== year ||
      date.getMonth() !== month - 1 ||
      date.getDate() !== day
    ) {
      return null;
    }
    return date;
  }

  function formatIsoDate(date) {
    return [
      date.getFullYear(),
      padDatePart(date.getMonth() + 1),
      padDatePart(date.getDate())
    ].join("-");
  }

  function formatReadableDate(date) {
    if (!date) return "ДД.ММ.ГГГГ";
    return [
      padDatePart(date.getDate()),
      padDatePart(date.getMonth() + 1),
      date.getFullYear()
    ].join(".");
  }

  function sameCalendarDay(first, second) {
    return Boolean(
      first &&
        second &&
        first.getFullYear() === second.getFullYear() &&
        first.getMonth() === second.getMonth() &&
        first.getDate() === second.getDate()
    );
  }

  function closeDatePicker(picker, restoreFocus) {
    if (!picker) return;
    picker.popup.hidden = true;
    picker.popup.style.top = "";
    picker.popup.style.bottom = "";
    picker.root.classList.remove("office-date-picker--open");
    picker.trigger.setAttribute("aria-expanded", "false");
    if (activeDatePicker === picker) activeDatePicker = null;
    if (restoreFocus) picker.trigger.focus();
  }

  function positionDatePicker(picker) {
    if (!picker || picker.popup.hidden) return;

    picker.popup.style.top = "";
    picker.popup.style.bottom = "";

    var rootRect = picker.root.getBoundingClientRect();
    var triggerRect = picker.trigger.getBoundingClientRect();
    var popupRect = picker.popup.getBoundingClientRect();
    var viewportHeight = window.innerHeight;
    var edgeGap = 12;
    var controlGap = 9;
    var spaceBelow = viewportHeight - triggerRect.bottom - edgeGap;
    var spaceAbove = triggerRect.top - edgeGap;
    var targetTop;

    if (spaceBelow >= popupRect.height) {
      targetTop = triggerRect.bottom + controlGap;
    } else if (spaceAbove >= popupRect.height) {
      targetTop = triggerRect.top - popupRect.height - controlGap;
    } else {
      targetTop = Math.min(
        Math.max(edgeGap, triggerRect.bottom + controlGap),
        Math.max(edgeGap, viewportHeight - popupRect.height - edgeGap)
      );
    }

    picker.popup.style.top = Math.round(targetTop - rootRect.top) + "px";
    picker.popup.style.bottom = "auto";
  }

  function renderDatePicker(picker) {
    var selectedDate = parseIsoDate(picker.input.value);
    var today = new Date();
    var year = picker.visibleMonth.getFullYear();
    var month = picker.visibleMonth.getMonth();
    var firstDay = new Date(year, month, 1);
    var gridOffset = (firstDay.getDay() + 6) % 7;
    var gridStart = new Date(year, month, 1 - gridOffset);

    picker.monthLabel.textContent = dateMonthNames[month] + " " + year;
    picker.days.replaceChildren();

    dateWeekdays.forEach(function (weekday) {
      var label = document.createElement("span");
      label.className = "office-date-picker__weekday";
      label.textContent = weekday;
      picker.days.appendChild(label);
    });

    for (var index = 0; index < 42; index += 1) {
      var date = new Date(
        gridStart.getFullYear(),
        gridStart.getMonth(),
        gridStart.getDate() + index
      );
      var dayButton = document.createElement("button");
      dayButton.type = "button";
      dayButton.className = "office-date-picker__day";
      dayButton.textContent = date.getDate();
      dayButton.dataset.date = formatIsoDate(date);
      dayButton.setAttribute(
        "aria-label",
        date.toLocaleDateString("ru-RU", {
          day: "numeric",
          month: "long",
          year: "numeric"
        })
      );

      if (date.getMonth() !== month) {
        dayButton.classList.add("office-date-picker__day--outside");
      }
      if (sameCalendarDay(date, today)) {
        dayButton.classList.add("office-date-picker__day--today");
      }
      if (sameCalendarDay(date, selectedDate)) {
        dayButton.classList.add("office-date-picker__day--selected");
        dayButton.setAttribute("aria-current", "date");
      }
      picker.days.appendChild(dayButton);
    }
  }

  function selectDate(picker, date) {
    picker.input.value = date ? formatIsoDate(date) : "";
    picker.value.textContent = formatReadableDate(date);
    picker.trigger.classList.toggle("office-date-picker__trigger--empty", !date);
    picker.input.dispatchEvent(new Event("input", { bubbles: true }));
    picker.input.dispatchEvent(new Event("change", { bubbles: true }));
    renderDatePicker(picker);
    closeDatePicker(picker, true);
  }

  document.querySelectorAll(".office-form input[type='date']").forEach(function (input, inputIndex) {
    if (input.dataset.officeDatePickerReady === "true") return;
    input.dataset.officeDatePickerReady = "true";

    var root = document.createElement("div");
    root.className = "office-date-picker";

    var trigger = document.createElement("button");
    trigger.type = "button";
    trigger.className = "office-date-picker__trigger";
    trigger.setAttribute("aria-haspopup", "dialog");
    trigger.setAttribute("aria-expanded", "false");
    trigger.innerHTML =
      '<span class="office-date-picker__value"></span>' +
      '<span class="office-date-picker__icon" aria-hidden="true"></span>';

    var popup = document.createElement("section");
    popup.className = "office-date-picker__popup";
    popup.hidden = true;
    popup.setAttribute("role", "dialog");
    popup.setAttribute("aria-modal", "false");
    popup.setAttribute("aria-label", "Выбор даты");
    popup.innerHTML =
      '<header class="office-date-picker__header">' +
      '<button type="button" data-date-previous aria-label="Предыдущий месяц">‹</button>' +
      '<strong></strong>' +
      '<button type="button" data-date-next aria-label="Следующий месяц">›</button>' +
      "</header>" +
      '<div class="office-date-picker__days"></div>' +
      '<footer class="office-date-picker__footer">' +
      '<button type="button" data-date-clear>Очистить</button>' +
      '<button type="button" data-date-today>Сегодня</button>' +
      "</footer>";

    input.parentNode.insertBefore(root, input);
    root.appendChild(input);
    root.appendChild(trigger);
    root.appendChild(popup);
    input.type = "hidden";

    var selectedDate = parseIsoDate(input.value);
    var initialDate = selectedDate || new Date();
    var picker = {
      input: input,
      root: root,
      trigger: trigger,
      value: trigger.querySelector(".office-date-picker__value"),
      popup: popup,
      monthLabel: popup.querySelector("strong"),
      days: popup.querySelector(".office-date-picker__days"),
      visibleMonth: new Date(initialDate.getFullYear(), initialDate.getMonth(), 1),
      index: inputIndex
    };

    picker.value.textContent = formatReadableDate(selectedDate);
    picker.trigger.classList.toggle("office-date-picker__trigger--empty", !selectedDate);
    picker.trigger.setAttribute("aria-controls", "office-date-picker-" + inputIndex);
    picker.popup.id = "office-date-picker-" + inputIndex;

    trigger.addEventListener("click", function () {
      if (activeDatePicker && activeDatePicker !== picker) {
        closeDatePicker(activeDatePicker, false);
      }
      if (!popup.hidden) {
        closeDatePicker(picker, false);
        return;
      }
      var currentDate = parseIsoDate(input.value) || new Date();
      picker.visibleMonth = new Date(currentDate.getFullYear(), currentDate.getMonth(), 1);
      renderDatePicker(picker);
      popup.hidden = false;
      root.classList.add("office-date-picker--open");
      trigger.setAttribute("aria-expanded", "true");
      activeDatePicker = picker;
      positionDatePicker(picker);
    });

    popup.querySelector("[data-date-previous]").addEventListener("click", function () {
      picker.visibleMonth = new Date(
        picker.visibleMonth.getFullYear(),
        picker.visibleMonth.getMonth() - 1,
        1
      );
      renderDatePicker(picker);
    });

    popup.querySelector("[data-date-next]").addEventListener("click", function () {
      picker.visibleMonth = new Date(
        picker.visibleMonth.getFullYear(),
        picker.visibleMonth.getMonth() + 1,
        1
      );
      renderDatePicker(picker);
    });

    picker.days.addEventListener("click", function (event) {
      var dayButton = event.target.closest("[data-date]");
      if (!dayButton) return;
      selectDate(picker, parseIsoDate(dayButton.dataset.date));
    });

    popup.querySelector("[data-date-clear]").addEventListener("click", function () {
      selectDate(picker, null);
    });

    popup.querySelector("[data-date-today]").addEventListener("click", function () {
      selectDate(picker, new Date());
    });

    renderDatePicker(picker);
  });

  document.addEventListener("click", function (event) {
    if (activeDatePicker && !activeDatePicker.root.contains(event.target)) {
      closeDatePicker(activeDatePicker, false);
    }
  });

  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && activeDatePicker) {
      closeDatePicker(activeDatePicker, true);
    }
  });

  window.addEventListener("resize", function () {
    if (activeDatePicker) positionDatePicker(activeDatePicker);
  });
})();
