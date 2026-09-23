/*
  AurumWeb public site JavaScript
  Responsibilities: mobile navigation, brief modal, public lead forms,
  lightweight chat widget and demo-page helpers.
*/

(function () {
  function closeMobileMenu() {
    var menu = document.querySelector("[data-menu]");
    if (menu) {
      menu.classList.remove("is-open");
    }
  }

  function postJson(url, data) {
    if (window.location.protocol === "file:") {
      return Promise.resolve({ ok: false, offline: true });
    }

    return fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify(data)
    }).then(function (response) {
      return response.text().then(function (text) {
        var payload = {};
        try {
          payload = text ? JSON.parse(text) : {};
        } catch (error) {
          payload = { ok: false, error: "Сервер вернул неожиданный ответ. Попробуйте еще раз." };
        }
        payload.httpOk = response.ok;
        payload.status = response.status;
        return payload;
      });
    });
  }

  function formToObject(form) {
    var formData = new FormData(form);
    var data = {};
    formData.forEach(function (value, key) {
      data[key] = value;
    });
    return data;
  }

  function focusFirstField(container) {
    var field = container.querySelector("input:not([type='hidden']), textarea, select");
    if (field && field.focus) {
      window.setTimeout(function () {
        field.focus({ preventScroll: true });
      }, 80);
    }
  }

  function textPreview(text) {
    var clean = String(text || "").replace(/\s+/g, " ").trim();
    if (!clean) {
      return "Заявка с сайта";
    }
    return clean.length > 180 ? clean.slice(0, 177) + "..." : clean;
  }

  document.querySelectorAll("[data-menu-toggle]").forEach(function (button) {
    button.addEventListener("click", function () {
      var menu = document.querySelector("[data-menu]");
      if (menu) {
        menu.classList.toggle("is-open");
      }
    });
  });

  document.querySelectorAll("[data-menu] a").forEach(function (link) {
    link.addEventListener("click", closeMobileMenu);
  });

  document.querySelectorAll("[data-lux-menu-toggle]").forEach(function (button) {
    button.addEventListener("click", function () {
      var header = button.closest("header");
      var menu = header ? header.querySelector("[data-lux-menu]") : document.querySelector("[data-lux-menu]");
      if (menu) {
        menu.classList.toggle("is-open");
      }
    });
  });

  document.querySelectorAll("[data-lux-menu] a").forEach(function (link) {
    link.addEventListener("click", function () {
      var menu = link.closest("[data-lux-menu]");
      if (menu) {
        menu.classList.remove("is-open");
      }
    });
  });

  var modal = document.querySelector("[data-brief-modal]");

  document.querySelectorAll("[data-open-brief]").forEach(function (button) {
    button.addEventListener("click", function () {
      if (!modal) return;
      closeMobileMenu();
      modal.classList.add("is-open");
      modal.setAttribute("aria-hidden", "false");
      document.body.classList.add("modal-open");
      focusFirstField(modal);
    });
  });

  document.querySelectorAll("[data-close-brief]").forEach(function (button) {
    button.addEventListener("click", function () {
      if (!modal) return;
      modal.classList.remove("is-open");
      modal.setAttribute("aria-hidden", "true");
      document.body.classList.remove("modal-open");
    });
  });

  if (modal) {
    modal.addEventListener("click", function (event) {
      if (event.target === modal) {
        modal.classList.remove("is-open");
        modal.setAttribute("aria-hidden", "true");
        document.body.classList.remove("modal-open");
      }
    });
  }

  var chatPanel = document.querySelector("[data-chat-panel]");
  var chatMessages = document.querySelector("[data-chat-messages]");
  var chatOpen = document.querySelector("[data-chat-open]");
  var chatClose = document.querySelector("[data-chat-close]");
  var messengerForm = document.querySelector(".messenger-form");

  function openChatPanel() {
    if (chatPanel) {
      chatPanel.classList.add("is-open");
    }
  }

  function setConversationToken(token) {
    if (!token || !messengerForm) return;
    var input = messengerForm.querySelector("input[name='conversation_token']");
    if (!input) {
      input = document.createElement("input");
      input.type = "hidden";
      input.name = "conversation_token";
      messengerForm.prepend(input);
    }
    input.value = token;
  }

  function appendChatMessage(author, text, modifier) {
    if (!chatMessages || !text) return;

    var message = document.createElement("p");
    message.className = "chat-panel__message";
    if (modifier) {
      message.className += " chat-panel__message--" + modifier;
    }

    var label = document.createElement("span");
    label.textContent = author;
    message.appendChild(label);
    message.appendChild(document.createTextNode(text));
    chatMessages.appendChild(message);
    chatMessages.scrollTop = chatMessages.scrollHeight;
  }

  function closeBriefModal() {
    if (!modal) return;
    modal.classList.remove("is-open");
    modal.setAttribute("aria-hidden", "true");
    document.body.classList.remove("modal-open");
  }

  function stopPreviewSubmit(form) {
    if (!form || !form.hasAttribute("data-preview-only")) {
      return false;
    }
    var status = form.querySelector(".form-status, [data-chat-status]");
    if (status) {
      status.textContent = "Это отдельное превью. Заявка не отправлена.";
    }
    return true;
  }

  document.querySelectorAll("form[data-lead-form]").forEach(function (form) {
    form.addEventListener("submit", function (event) {
      event.preventDefault();
      if (stopPreviewSubmit(form)) {
        return;
      }
      if (form.reportValidity && !form.reportValidity()) {
        return;
      }
      var data = formToObject(form);
      data.page = document.title;
      var submittedTask = data.task || data.message || data.body || "";
      var submitButton = form.querySelector("button[type='submit']");
      var status = form.querySelector(".form-status");
      if (status) {
        status.textContent = "Отправляю заявку...";
      }
      if (submitButton) {
        submitButton.disabled = true;
        submitButton.setAttribute("aria-busy", "true");
      }
      postJson("/api/leads/", data)
        .then(function (payload) {
          if (status) {
            status.textContent = payload.ok
              ? payload.message
              : (payload.error || "Не удалось отправить заявку. Попробуйте еще раз.");
          }
          if (payload.ok) {
            form.reset();
            if (payload.conversation_token) {
              setConversationToken(payload.conversation_token);
            }
            closeBriefModal();
            appendChatMessage("Вы", textPreview(submittedTask), "client");
            appendChatMessage("AurumWeb", payload.message || "Заявка принята. Я вернусь с ответом после разбора.", "system");
            openChatPanel();
            if (messengerForm) {
              focusFirstField(messengerForm);
            }
          } else if (status && status.scrollIntoView) {
            status.scrollIntoView({ block: "center", behavior: "smooth" });
          }
        })
        .catch(function () {
          if (status) {
            status.textContent = "Не удалось отправить заявку. Проверьте соединение и попробуйте еще раз.";
          }
        })
        .finally(function () {
          if (submitButton) {
            submitButton.disabled = false;
            submitButton.removeAttribute("aria-busy");
          }
        });
    });
  });

  if (chatOpen && chatPanel) {
    chatOpen.addEventListener("click", function () {
      openChatPanel();
    });
  }

  if (chatClose && chatPanel) {
    chatClose.addEventListener("click", function () {
      chatPanel.classList.remove("is-open");
    });
  }

  if (messengerForm && chatMessages) {
    messengerForm.addEventListener("submit", function (event) {
      event.preventDefault();
      if (stopPreviewSubmit(messengerForm)) {
        return;
      }
      if (messengerForm.reportValidity && !messengerForm.reportValidity()) {
        return;
      }
      var data = formToObject(messengerForm);
      var input = messengerForm.querySelector("input");
      if (!input || !input.value.trim()) return;

      var status = messengerForm.querySelector("[data-chat-status]");
      var sentText = input.value.trim();
      var submitButton = messengerForm.querySelector("button[type='submit']");
      if (status) {
        status.textContent = "Отправляю сообщение...";
      }
      if (submitButton) {
        submitButton.disabled = true;
        submitButton.setAttribute("aria-busy", "true");
      }
      postJson("/messenger/api/messages/", {
        conversation_token: data.conversation_token || "",
        message: data.message || sentText,
        title: document.title,
        personal_data_consent: data.personal_data_consent || ""
      })
        .then(function (payload) {
          if (!payload.ok) {
            if (status) {
              status.textContent = payload.error || "Не удалось отправить сообщение. Попробуйте еще раз.";
            }
            return;
          }

          appendChatMessage("Вы", sentText, "client");
          messengerForm.reset();
          if (payload.conversation_token) {
            setConversationToken(payload.conversation_token);
          }
          if (payload.auto_reply) {
            appendChatMessage("AurumWeb", payload.auto_reply, "system");
          }

          if (status) {
            status.textContent = "Сообщение отправлено.";
          }
        })
        .catch(function () {
          if (status) {
            status.textContent = "Не удалось отправить сообщение. Проверьте соединение и попробуйте еще раз.";
          }
        })
        .finally(function () {
          if (submitButton) {
            submitButton.disabled = false;
            submitButton.removeAttribute("aria-busy");
          }
        });
    });
  }

  document.querySelectorAll("[data-carousel]").forEach(function (carousel) {
    var track = carousel.querySelector("[data-carousel-track]");
    var prev = carousel.querySelector("[data-carousel-control='prev']");
    var next = carousel.querySelector("[data-carousel-control='next']");

    if (!track || !prev || !next) {
      return;
    }

    function scrollAmount() {
      return Math.max(280, Math.floor(track.clientWidth * 0.82));
    }

    function updateButtons() {
      var maxScroll = Math.max(0, track.scrollWidth - track.clientWidth - 2);
      prev.disabled = track.scrollLeft <= 2;
      next.disabled = track.scrollLeft >= maxScroll;
    }

    prev.addEventListener("click", function () {
      track.scrollBy({ left: -scrollAmount(), behavior: "smooth" });
    });

    next.addEventListener("click", function () {
      track.scrollBy({ left: scrollAmount(), behavior: "smooth" });
    });

    track.addEventListener("scroll", updateButtons, { passive: true });
    window.addEventListener("resize", updateButtons);
    updateButtons();
  });

  document.querySelectorAll("form[data-preview-only]:not(.lead-form):not(.messenger-form)").forEach(function (form) {
    form.addEventListener("submit", function (event) {
      event.preventDefault();
      var status = form.querySelector(".form-status, [data-chat-status]");
      if (status) {
        status.textContent = "Заявка готова к отправке. В этой копии данные не уходят на сервер.";
      }
    });
  });

  document.querySelectorAll("[data-profile-nav]").forEach(function (nav) {
    nav.querySelectorAll("a").forEach(function (link) {
      link.addEventListener("click", function () {
        nav.querySelectorAll("a").forEach(function (item) {
          item.classList.toggle("is-active", item === link);
        });
      });
    });
  });
})();
