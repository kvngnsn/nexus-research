/** Small, shared browser helpers. No build step or runtime dependencies. */
export const $ = (id) => document.getElementById(id);

export const escapeHtml = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (character) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[character],
  );

export function downloadJson(name, data) {
  if (window.webkit?.messageHandlers?.save) {
    window.webkit.messageHandlers.save.postMessage({ name, text: JSON.stringify(data, null, 2) });
    return;
  }
  const url = URL.createObjectURL(
    new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }),
  );
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
