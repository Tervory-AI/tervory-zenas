/* B2: mount in the store document so B1 receives the store's Origin, never an iframe origin. */
(() => {
  "use strict";
  const script = document.currentScript;
  const mount = () => {
    if (
      !script ||
      !script.dataset.botKey ||
      document.querySelector("[data-trigger-widget]")
    )
      return;
    const origin = new URL(script.src).origin,
      key = script.dataset.botKey;
    if (!/^tb1\.(us-east|za)\.[a-f0-9]{32}\.rk_pub_[a-f0-9]{48}$/.test(key))
      return;
    const host = document.createElement("div");
    host.style.visibility = "hidden";
    host.setAttribute("data-trigger-widget", "");
    // FontFace registration works across the shadow boundary without changing store CSS.
    if (typeof FontFace !== "undefined") {
      for (const [family, file] of [
        ["TriggerWidgetAtkinson", "AtkinsonHyperlegible-Regular.ttf"],
        ["TriggerWidgetLiterata", "Literata.ttf"],
      ]) {
        const face = new FontFace(family, `url("${origin}/trigger/${file}")`);
        face
          .load()
          .then((loaded) => document.fonts.add(loaded))
          .catch(() => {});
      }
    }
    const shadow = host.attachShadow({ mode: "open" });
    document.body.append(host);
    const element = (tag, text, className) => {
      const node = document.createElement(tag);
      if (text !== undefined) node.textContent = text;
      if (className) node.className = className;
      return node;
    };
    const css = element("link");
    css.rel = "stylesheet";
    css.addEventListener(
      "load",
      () => {
        host.style.visibility = "";
      },
      { once: true },
    );
    css.addEventListener("error", () => host.remove(), { once: true });
    css.href = origin + "/trigger/widget.css";
    shadow.append(css);
    let config = {},
      conversation = null,
      visitor = null,
      pending = false,
      poller = null,
      opened = false;
    const seen = new Set(),
      launcher = element("button", "Chat", "launcher"),
      panel = element("section", undefined, "panel");
    launcher.type = "button";
    launcher.setAttribute("aria-expanded", "false");
    panel.setAttribute("aria-label", "Chat");
    panel.hidden = true;
    shadow.append(launcher, panel);
    const api = async (operation, body = {}) => {
      const response = await fetch(origin + "/v1/bot/widget/" + operation, {
        method: "POST",
        mode: "cors",
        credentials: "omit",
        redirect: "error",
        cache: "no-store",
        headers: { "content-type": "application/json", "x-bot-key": key },
        body: JSON.stringify(body),
        signal: AbortSignal.timeout(90000),
      });
      if (!response.ok) throw new Error(String(response.status));
      return response.json();
    };
    const identity = () =>
      conversation
        ? { conversation_id: conversation, visitor_id: visitor }
        : {};
    const remember = (result) => {
      if (result.conversation_id && result.visitor_id) {
        conversation = result.conversation_id;
        visitor = result.visitor_id;
      }
    };
    const button = (label, click) => {
      const node = element("button", label);
      node.type = "button";
      node.addEventListener("click", click);
      return node;
    };
    const close = () => {
      opened = false;
      panel.hidden = true;
      launcher.hidden = false;
      launcher.setAttribute("aria-expanded", "false");
      clearInterval(poller);
      poller = null;
      launcher.focus();
    };
    const header = element("header"),
      name = element("h2"),
      closeButton = button("\u00d7", close);
    closeButton.setAttribute("aria-label", "Close");
    header.append(name, closeButton);
    const body = element("div", undefined, "messages"),
      footer = element("form", undefined, "composer");
    body.setAttribute("aria-live", "polite");
    panel.append(header, body, footer);
    const bubble = (role, text, person) => {
      const box = element("div", undefined, "bubble " + role.toLowerCase());
      box.append(element("p", role));
      if (person) box.append(element("p", person));
      box.append(element("p", text ?? ""));
      body.append(box);
      return box;
    };
    const status = (text) => {
      if (text) body.append(element("p", text, "status"));
    };
    const unavailable = () => status(config.no_source_message);
    const setPending = (value) => {
      pending = value;
      panel.setAttribute("aria-busy", String(value));
      panel.querySelectorAll("button,input,textarea").forEach((node) => {
        if (node !== closeButton)
          node.disabled = value || node.dataset.unavailable === "true";
      });
    };
    const poll = async () => {
      if (!opened || !conversation || pending) return;
      try {
        const result = await api("poll", identity());
        for (const message of result.messages ?? []) {
          if (!seen.has(message.id)) {
            seen.add(message.id);
            bubble("Person", message.answer, message.person);
          }
        }
      } catch {
        /* Keep existing transcript; never fabricate a person or answer. */
      }
    };
    const startPoll = () => {
      if (!poller) poller = setInterval(poll, 30000);
    };
    const actions = () => {
      body.querySelector(".actions")?.remove();
      const area = element("div", undefined, "actions");
      area.append(
        button("Talk to a person", async () => {
          if (pending) return;
          setPending(true);
          try {
            remember(await api("handover", identity()));
            status("Connecting you to a person");
            if (config.handover_message) status(config.handover_message);
            startPoll();
          } catch {
            unavailable();
          } finally {
            setPending(false);
          }
        }),
      );
      const details = button("Leave details", () => leadForm(false)),
        quote = button("Request a quote", () => leadForm(true));
      details.disabled = !config.capture;
      quote.disabled = !config.quote;
      details.dataset.unavailable = String(!config.capture);
      quote.dataset.unavailable = String(!config.quote);
      area.append(details, quote);
      body.append(area);
    };
    const answer = (result) => {
      remember(result);
      if (result.answer && result.sources?.length) {
        const box = bubble("Bot", result.answer);
        box.append(element("p", "Sources"));
        const sources = element("div", undefined, "sources");
        for (const source of result.sources) {
          let url;
          try {
            const candidate = new URL(source.url);
            if (
              ["https:", "http:"].includes(candidate.protocol) &&
              !candidate.username &&
              !candidate.password
            )
              url = candidate;
          } catch {
            /* An approved document or FAQ may have no public Source link. */
          }
          const chip = element(url ? "a" : "span", undefined, "source");
          if (url) {
            chip.href = url.href;
            chip.target = "_blank";
            chip.rel = "noopener noreferrer";
          }
          chip.append(element("span", source.title));
          if (url) chip.append(element("small", url.hostname));
          sources.append(chip);
        }
        box.append(sources);
      } else {
        unavailable();
        if (result.state === "handed_over") {
          status("Connecting you to a person");
          if (config.handover_message) status(config.handover_message);
          startPoll();
        }
      }
      actions();
    };
    const composer = () => {
      footer.replaceChildren();
      const label = element("label", "Message"),
        input = element("textarea"),
        send = element("button", "Send");
      input.name = "message";
      input.maxLength = 2000;
      input.required = true;
      input.rows = 1;
      label.append(input);
      send.type = "submit";
      footer.append(label, send);
      footer.onsubmit = async (event) => {
        event.preventDefault();
        if (pending || !input.value.trim()) return;
        const text = input.value;
        input.value = "";
        body.querySelector(".actions")?.remove();
        bubble("You", text);
        setPending(true);
        try {
          answer(await api("message", { ...identity(), text }));
        } catch {
          unavailable();
          actions();
        } finally {
          setPending(false);
          const questions = body.querySelectorAll(".bubble.you");
          const latest = questions[questions.length - 1];
          if (latest)
            body.scrollTop +=
              latest.getBoundingClientRect().top -
              body.getBoundingClientRect().top -
              16;
        }
      };
    };
    function leadForm(quote) {
      if (!config.capture || (quote && !config.quote)) return;
      body.replaceChildren();
      footer.replaceChildren();
      footer.onsubmit = null;
      body.append(
        element("h3", quote ? "Request a quote" : "Leave your details"),
      );
      const form = element("form"),
        inputs = {};
      const fields = quote
        ? [
            ["title", "Title"],
            ["product", "Product"],
            ["quantity", "Quantity"],
            ["request", "Your request"],
            ["contact", "Contact"],
          ]
        : [
            ["first", "First name"],
            ["last", "Last name"],
            ["email", "Email"],
            ["phone", "Phone"],
            ["notes", "Notes"],
          ];
      for (const [key, labelText] of fields) {
        const label = element("label", labelText),
          input = element(
            key === "notes" || key === "request" ? "textarea" : "input",
          );
        input.name = key;
        input.maxLength = key === "notes" || key === "request" ? 1600 : 100;
        input.required = quote || key === "first";
        if (key === "email") input.type = "email";
        if (key === "contact") {
          input.pattern = ".+ <[^<> ]+@[^<> ]+>";
          input.maxLength = 360;
        }
        if (key === "phone") {
          input.type = "tel";
          input.maxLength = 50;
        }
        if (key === "quantity") {
          input.type = "number";
          input.min = "1";
          input.max = "100000";
        }
        inputs[key] = input;
        label.append(input);
        form.append(label);
      }
      form.append(element("p", config.privacy_notice, "privacy"));
      const submit = element(
        "button",
        quote ? "Send request" : "Send my details",
      );
      submit.type = "submit";
      form.append(submit);
      body.append(form);
      form.onsubmit = async (event) => {
        event.preventDefault();
        if (pending) return;
        const vals = Object.fromEntries(
          Object.entries(inputs).map(([key, node]) => [key, node.value.trim()]),
        );
        setPending(true);
        try {
          const contact = quote
            ? {
                name: vals.contact
                  .slice(0, vals.contact.lastIndexOf("<"))
                  .trim(),
                email: vals.contact
                  .slice(vals.contact.lastIndexOf("<") + 1, -1)
                  .trim(),
              }
            : {
                name: [vals.first, vals.last].filter(Boolean).join(" "),
                first_name: vals.first,
                last_name: vals.last,
                email: vals.email || undefined,
                phone: vals.phone || undefined,
              };
          const result = await api("message", {
            ...identity(),
            text: quote ? vals.request : vals.notes || "Leave details",
            contact,
            notice_version: config.notice_version,
            ...(quote
              ? {
                  quote_product_id: vals.product,
                  quote_title: vals.title,
                  quote_quantity: Number(vals.quantity),
                }
              : {}),
          });
          remember(result);
          if (
            (quote && !result.quote_draft_id) ||
            (!quote && !result.lead_draft_id)
          ) {
            unavailable();
            return;
          }
          body.replaceChildren(
            element("p", quote ? "Request sent" : "Details sent"),
          );
          composer();
          actions();
        } catch {
          unavailable();
        } finally {
          setPending(false);
        }
      };
    }
    launcher.addEventListener("click", async () => {
      opened = true;
      panel.hidden = false;
      launcher.hidden = true;
      launcher.setAttribute("aria-expanded", "true");
      closeButton.focus();
      if (!body.childNodes.length) {
        try {
          config = await api("config");
          name.textContent = config.name ?? "";
          for (const property of ["accent", "ink", "paper", "line"]) {
            if (/^#[a-f\d]{6}$/i.test(config[property] ?? ""))
              host.style.setProperty("--client-" + property, config[property]);
          }
          if (config.greeting) status(config.greeting);
          const start = button("Start chat", () => {
            body.replaceChildren();
            composer();
            actions();
            footer.querySelector("textarea")?.focus();
          });
          body.append(start);
          if (!config.available) {
            unavailable();
            actions();
          }
        } catch {
          panel.hidden = true;
          launcher.hidden = false;
          opened = false;
        }
      } else startPoll();
    });
    panel.addEventListener("keydown", (event) => {
      if (event.key === "Escape") close();
    });
  };
  if (document.readyState === "loading")
    document.addEventListener("DOMContentLoaded", mount, { once: true });
  else mount();
})();
