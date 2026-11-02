// dagalab extras for Glance (loaded from glance.yml document.head).
// 1. Light tiles: tap toggles the room through Home Assistant's light-toggle webhook (secret ID, HA_LIGHT_WEBHOOK)
//    (helm/charts/home-assistant-packages/packages/glance.yaml). A form POST is a "simple" cross-origin request, so no
//    CORS preflight is needed; the response is opaque, so the tile flips optimistically (the next refresh shows HA's real state).
// 2. Glance renders a page once: refresh its content when the tab/PWA comes back after a while.
(function () {
  "use strict";

  document.addEventListener("click", function (event) {
    var tile = event.target.closest(".dl-light[data-entity]");
    if (!tile || tile.disabled) return;
    var grid = tile.closest("[data-ha-webhook]");
    if (!grid) return;

    tile.classList.add("is-pending");
    fetch(grid.dataset.haWebhook, {
      method: "POST",
      mode: "no-cors",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: "entity_id=" + encodeURIComponent(tile.dataset.entity),
    })
      .then(function () {
        var on = tile.classList.toggle("is-on");
        var sub = tile.querySelector(".dl-tile-sub");
        if (sub) sub.textContent = on ? "on" : "off";
      })
      .catch(function () {
        tile.classList.add("is-down");
      })
      .finally(function () {
        tile.classList.remove("is-pending");
      });
  });

  var hiddenAt = 0;
  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "hidden") {
      hiddenAt = Date.now();
    } else if (hiddenAt && Date.now() - hiddenAt > 5 * 60 * 1000) {
      location.reload();
    }
  });
})();
