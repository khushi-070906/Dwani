/* The account avatar: the first letter of the person's name on a soft tint, drawn here (no network, nothing about the person
   is sent anywhere). One colour per person, picked from the same muted palette as the rest of the site. */
(function () {
  var TONES = [["#1F5E4D", "#E4EEEA"], ["#1E2A44", "#E3E6EE"], ["#A5503A", "#F4E7E2"], ["#3D6A87", "#E2ECF2"], ["#8A5B0F", "#F5EBD8"]];
  function initial(name, email) {
    var s = String(name || "").trim() || String(email || "").trim();
    var ch = Array.from(s)[0] || "?";
    return ch.toUpperCase();
  }
  function pick(seed) {
    var h = 0, s = String(seed || "");
    for (var i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0;
    return TONES[h % TONES.length];
  }
  function esc(c) { return c.replace(/[&<>"']/g, function (m) { return "&#" + m.charCodeAt(0) + ";"; }); }
  window.dwaniAvatar = function (name, email) {
    var tone = pick(email || name), ch = esc(initial(name, email));
    var svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" fill="' + tone[1] + '"/>' +
      '<text x="32" y="33" text-anchor="middle" dominant-baseline="central" font-family="Mukta, \'Noto Sans\', \'Segoe UI\', system-ui, sans-serif" ' +
      'font-size="30" font-weight="700" fill="' + tone[0] + '">' + ch + "</text></svg>";
    return "data:image/svg+xml;charset=utf-8," + encodeURIComponent(svg);
  };
})();
