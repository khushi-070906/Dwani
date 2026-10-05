/* Shared site navigation behaviour (mobile menu + logged-in state), matching the
   DwaniLive home page. Used by pages that include static/site-nav.css. */
(function () {
  "use strict";
  var links = document.getElementById("nav-links");
  var burger = document.getElementById("nav-hamburger");
  if (burger && links) {
    burger.addEventListener("click", function () {
      var open = links.classList.toggle("mobile-open");
      burger.setAttribute("aria-expanded", open ? "true" : "false");
    });
    document.addEventListener("click", function (e) {
      if (!links.classList.contains("mobile-open") || links.contains(e.target) || burger.contains(e.target)) return;
      links.classList.remove("mobile-open");
      burger.setAttribute("aria-expanded", "false");
    });
  }

  var right = document.getElementById("nav-right");
  if (!right) return;
  var CACHE_KEY = "dwani_nav_user";   // same key as the home page, so the nav looks identical across pages
  function show(user) {
    var acct = document.getElementById("nav-account"), cta = document.getElementById("nav-login-cta"),
        dash = document.getElementById("nav-dashboard-btn");
    if (user) {
      var label = (user.name && user.name.trim()) || user.email;
      var avatar = "https://api.dicebear.com/10.x/avataaars/svg?seed=" + encodeURIComponent(user.email) +
        (user.gender === "male" ? "&facialHairProbability=30" : user.gender === "female" ? "&facialHairProbability=0" : "&facialHairProbability=10");
      document.getElementById("nav-avatar").src = avatar;
      document.getElementById("nav-name-label").textContent = label;
      acct.style.display = "flex"; if (dash) dash.style.display = "flex"; cta.style.display = "none";
    } else {
      acct.style.display = "none"; if (dash) dash.style.display = "none"; cta.style.display = "";
    }
  }
  var cached = null;
  try { cached = JSON.parse(sessionStorage.getItem(CACHE_KEY)); } catch (e) {}
  show(cached);
  right.classList.add("ready");
  fetch("/api/me", { credentials: "include" }).then(function (r) { return r.ok ? r.json() : null; }).then(function (user) {
    if (user) { try { sessionStorage.setItem(CACHE_KEY, JSON.stringify({ name: user.name, email: user.email, gender: user.gender })); } catch (e) {} }
    else { try { sessionStorage.removeItem(CACHE_KEY); } catch (e) {} }
    show(user);
  }).catch(function () {});
})();
