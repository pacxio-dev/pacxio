// Cookies are shared across ports on the same loopback host. Each job can have its own port.
const preference = "pacxio_studio_lang";
// Outside the page (the 3D viewer loaded on its own, e.g. in tests) there is no document: use English.
const inPage = typeof document !== "undefined";
const saved = inPage ? document.cookie.split("; ").find((item) => item.startsWith(preference + "="))?.split("=")[1] : null;
const language = saved === "en" || saved === "zh"
  ? saved
  : inPage && (navigator.language || "").toLowerCase().startsWith("zh") ? "zh" : "en";
// If the Chinese table cannot be loaded, show English rather than a blank page.
const catalog = language === "zh" ? await fetch("/api/ui-zh").then((r) => r.json()).catch(() => ({})) : {};

export function tr(message, ...args) {
  const text = Object.hasOwn(catalog, message) ? catalog[message] : message;
  return text.replace(/\{([1-9]\d*)\}/g, (field, index) => args[Number(index) - 1] ?? field);
}

export function apiUrl(path) {
  return path + (path.includes("?") ? "&" : "?") + "lang=" + language;
}

export function localizePage() {
  document.documentElement.lang = language === "zh" ? "zh-CN" : "en";
  for (const element of document.querySelectorAll("[data-l10n]")) {
    for (const field of element.dataset.l10n.split(" ")) {
      if (field === "text") element.textContent = tr(element.textContent);
      else element.setAttribute(field, tr(element.getAttribute(field)));
    }
  }
  const toggle = document.querySelector("#language-toggle");
  const target = language === "zh" ? "en" : "zh";
  toggle.lang = target === "zh" ? "zh-CN" : "en";
  toggle.textContent = target === "zh" ? "中文" : "EN";
  toggle.title = target === "zh" ? "切换到中文" : "Switch to English";
  toggle.hidden = false;
  toggle.addEventListener("click", () => {
    document.cookie = preference + "=" + target + "; Max-Age=31536000; Path=/; SameSite=Lax";
    location.reload();
  });
}
