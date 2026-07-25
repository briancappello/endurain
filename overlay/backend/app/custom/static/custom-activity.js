/**
 * Custom Endurain UI enhancements for activity detail pages.
 *
 * Injected into the SPA via a <script> tag in index.html.
 * Adds:
 * - Trail description below the activity title
 * - Segments table below the map, above the Gear section
 * - Segment highlight on map (orange SVG overlay) when hovering table rows
 */
(function () {
  "use strict";

  const API_BASE = "/api/v1/custom";
  const HIGHLIGHT_COLOR = "#f97316";
  const HIGHLIGHT_WEIGHT = 5;
  const HIGHLIGHT_OPACITY = 0.9;
  const DEBUG = true;

  let currentActivityId = null;
  let highlightSvg = null;
  let leafletMap = null;

  function log(...args) {
    if (DEBUG) console.log("[custom]", ...args);
  }

  // --- Utilities ---

  function getActivityIdFromUrl() {
    const m = window.location.pathname.match(/\/activity\/(\d+)/);
    return m ? parseInt(m[1]) : null;
  }

  async function fetchJson(url) {
    try {
      const r = await fetch(url);
      return r.ok ? await r.json() : null;
    } catch {
      return null;
    }
  }

  function fmt(seconds) {
    if (!seconds && seconds !== 0) return "\u2014";
    const m = Math.floor(seconds / 60);
    const s = seconds % 60;
    return `${m}:${s.toString().padStart(2, "0")}`;
  }

  function fmtDist(meters) {
    if (!meters) return "\u2014";
    return meters >= 1000
      ? `${(meters / 1000).toFixed(1)} km`
      : `${Math.round(meters)} m`;
  }

  function fmtGrade(g) {
    return g !== null && g !== undefined ? `${g.toFixed(1)}%` : "\u2014";
  }

  function fmtNum(n) {
    return n ? Math.round(n).toString() : "\u2014";
  }

  // --- Find Leaflet map instance ---

  function findLeafletMap() {
    const el = document.querySelector(".leaflet-container");
    if (!el) {
      log("No .leaflet-container found");
      return null;
    }

    // The ActivityMapComponent is patched to expose the map instance
    // as _leafletMapRef on the container element.
    if (el._leafletMapRef) {
      log("Found Leaflet map via _leafletMapRef");
      return el._leafletMapRef;
    }

    log("No _leafletMapRef on container");
    return null;
  }

  // --- Trail Description ---

  function injectTrailDescription(data) {
    if (!data || !data.description) return;
    if (document.getElementById("custom-trail-desc")) return;

    const h1 = document.querySelector("h1");
    if (!h1) {
      log("No h1 found for trail description");
      return;
    }

    const p = document.createElement("p");
    p.id = "custom-trail-desc";
    p.style.cssText =
      "font-size:0.9rem;margin-top:0.25rem;margin-bottom:0.75rem;color:var(--bs-secondary-color, #adb5bd);font-style:italic;display:block;";
    p.textContent = data.description;
    // Insert after the h1. If h1 is followed by a sibling, insert before it.
    // Otherwise insert at end of h1's parent.
    if (h1.nextElementSibling) {
      h1.parentNode.insertBefore(p, h1.nextElementSibling);
    } else {
      h1.parentNode.appendChild(p);
    }
    log("Trail description injected:", data.description);
  }

  // --- Segments Table ---

  function injectSegmentsTable(segments) {
    if (!segments || segments.length === 0) return;
    if (document.getElementById("custom-segments")) return;

    // Find the <hr> after the map section
    const mapEl = document.querySelector(".leaflet-container");
    if (!mapEl) {
      log("No .leaflet-container for segments table");
      return;
    }

    let mapSection = mapEl.closest(".mt-3.mb-3") || mapEl.parentElement;
    let insertPoint = null;
    let sibling = mapSection?.nextElementSibling;
    while (sibling) {
      if (sibling.tagName === "HR") {
        insertPoint = sibling;
        break;
      }
      sibling = sibling.nextElementSibling;
    }

    if (!insertPoint) {
      log("No <hr> found after map section");
      return;
    }

    const wrapper = document.createElement("div");
    wrapper.id = "custom-segments";
    wrapper.style.cssText = "margin:0.75rem 0;padding:0;";

    // Heading
    const heading = document.createElement("div");
    heading.style.cssText =
      "display:flex;align-items:center;gap:0.5rem;margin-bottom:0.5rem;";
    heading.innerHTML =
      '<span style="font-weight:600;font-size:0.95rem;">Segments</span>' +
      '<span style="color:#9ca3af;font-size:0.8rem;">(' +
      segments.length +
      ")</span>";
    wrapper.appendChild(heading);

    // Table
    const tableWrap = document.createElement("div");
    tableWrap.style.cssText = "overflow-x:auto;";

    const table = document.createElement("table");
    table.className = "table table-sm";
    table.style.cssText = "font-size:0.83rem;margin-bottom:0;";

    const thead = document.createElement("thead");
    const headerRow = document.createElement("tr");
    [
      ["Name", "min-width:180px;text-align:left"],
      ["Time", "width:65px;text-align:right"],
      ["PR", "width:40px;text-align:right"],
      ["Dist", "width:65px;text-align:right"],
      ["Grade", "width:55px;text-align:right"],
      ["HR", "width:50px;text-align:right"],
      ["W", "width:50px;text-align:right"],
    ].forEach(([text, style]) => {
      const th = document.createElement("th");
      th.textContent = text;
      th.style.cssText = style;
      headerRow.appendChild(th);
    });
    thead.appendChild(headerRow);
    table.appendChild(thead);

    const tbody = document.createElement("tbody");
    for (const seg of segments) {
      const tr = document.createElement("tr");
      tr.style.cssText = "cursor:pointer;";

      const cells = [
        seg.name,
        fmt(seg.elapsed_time),
        seg.pr_rank === 1
          ? "PR"
          : seg.pr_rank
            ? String(seg.pr_rank)
            : "\u2014",
        fmtDist(seg.segment_distance),
        fmtGrade(seg.average_grade),
        fmtNum(seg.average_heartrate),
        fmtNum(seg.average_watts),
      ];

      cells.forEach((text, i) => {
        const td = document.createElement("td");
        if (i === 0) {
          td.textContent = text;
        } else {
          td.textContent = text;
          td.style.textAlign = "right";
        }
        // PR badge styling
        if (i === 2 && seg.pr_rank === 1) {
          td.style.color = "#f59e0b";
          td.style.fontWeight = "700";
          td.title = "Personal Record";
        }
        tr.appendChild(td);
      });

      // Hover: highlight row and segment on map
      // Use setProperty with !important to override Bootstrap's dark table styles
      tr.onmouseenter = function () {
        this.style.setProperty("background-color", "rgba(249, 115, 22, 0.2)", "important");
        for (const td of this.children) {
          td.style.setProperty("background-color", "transparent", "important");
        }
        highlightSegmentOnMap(seg);
      };
      tr.onmouseleave = function () {
        this.style.removeProperty("background-color");
        for (const td of this.children) {
          td.style.removeProperty("background-color");
        }
        clearMapHighlight();
      };

      tbody.appendChild(tr);
    }

    table.appendChild(tbody);
    tableWrap.appendChild(table);
    wrapper.appendChild(tableWrap);

    insertPoint.insertAdjacentElement("beforebegin", wrapper);
    log("Segments table injected with", segments.length, "rows");
  }

  // --- Map Segment Highlighting via SVG overlay ---

  function highlightSegmentOnMap(seg) {
    clearMapHighlight();
    if (!seg.latlngs || seg.latlngs.length < 2) {
      log("No latlngs for segment:", seg.name);
      return;
    }

    const mapContainer = document.querySelector(".leaflet-container");
    if (!mapContainer) return;

    if (!leafletMap) {
      leafletMap = findLeafletMap();
    }

    if (!leafletMap || !leafletMap.latLngToContainerPoint) {
      log("No Leaflet map with latLngToContainerPoint");
      return;
    }

    // Project lat/lng to pixel coordinates relative to the map container
    const points = [];
    for (const ll of seg.latlngs) {
      try {
        const pt = leafletMap.latLngToContainerPoint([ll[0], ll[1]]);
        points.push(`${pt.x},${pt.y}`);
      } catch (e) {
        // skip invalid points
      }
    }

    if (points.length < 2) {
      log("Not enough projected points for segment:", seg.name);
      return;
    }

    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.id = "custom-segment-highlight";
    svg.setAttribute(
      "style",
      "position:absolute;top:0;left:0;width:100%;height:100%;pointer-events:none;z-index:600;"
    );

    const polyline = document.createElementNS(
      "http://www.w3.org/2000/svg",
      "polyline"
    );
    polyline.setAttribute("points", points.join(" "));
    polyline.setAttribute("fill", "none");
    polyline.setAttribute("stroke", HIGHLIGHT_COLOR);
    polyline.setAttribute("stroke-width", String(HIGHLIGHT_WEIGHT));
    polyline.setAttribute("stroke-opacity", String(HIGHLIGHT_OPACITY));
    polyline.setAttribute("stroke-linejoin", "round");
    polyline.setAttribute("stroke-linecap", "round");

    svg.appendChild(polyline);
    mapContainer.appendChild(svg);
    highlightSvg = svg;
    log("Highlighted segment:", seg.name, "with", points.length, "points");
  }

  function clearMapHighlight() {
    if (highlightSvg) {
      highlightSvg.remove();
      highlightSvg = null;
    }
  }

  // --- Orchestration ---

  async function onActivityPage(activityId) {
    if (activityId === currentActivityId) return;
    currentActivityId = activityId;
    leafletMap = null;

    const [segments, trails] = await Promise.all([
      fetchJson(`${API_BASE}/activities/${activityId}/segments`),
      fetchJson(`${API_BASE}/activities/${activityId}/trail-description`),
    ]);

    log("Data loaded. Segments:", segments?.length, "Trails:", trails?.description);

    // Wait for Vue to finish rendering, then keep retrying injection
    // until both elements are placed (the map may load asynchronously)
    let attempts = 0;
    const inject = () => {
      attempts++;
      let done = true;

      if (!document.getElementById("custom-trail-desc") && trails?.description) {
        injectTrailDescription(trails);
        if (!document.getElementById("custom-trail-desc")) done = false;
      }

      if (!document.getElementById("custom-segments") && segments?.length > 0) {
        injectSegmentsTable(segments);
        if (!document.getElementById("custom-segments")) done = false;
      }

      if (!done && attempts < 20) {
        setTimeout(inject, 500);
      } else {
        log("Injection complete after", attempts, "attempts");
      }
    };

    setTimeout(inject, 500);
  }

  function cleanup() {
    clearMapHighlight();
    currentActivityId = null;
    leafletMap = null;
    for (const id of ["custom-trail-desc", "custom-segments"]) {
      const el = document.getElementById(id);
      if (el) el.remove();
    }
  }

  // --- SPA route detection ---

  let lastUrl = "";
  function checkRoute() {
    const url = window.location.pathname;
    if (url === lastUrl) return;
    lastUrl = url;

    const id = getActivityIdFromUrl();
    if (id) {
      waitFor("h1", 5000).then(() => onActivityPage(id));
    } else {
      cleanup();
    }
  }

  function waitFor(sel, timeout) {
    return new Promise((resolve) => {
      if (document.querySelector(sel)) return resolve();
      const obs = new MutationObserver(() => {
        if (document.querySelector(sel)) {
          obs.disconnect();
          resolve();
        }
      });
      obs.observe(document.body, { childList: true, subtree: true });
      setTimeout(() => {
        obs.disconnect();
        resolve();
      }, timeout);
    });
  }

  setInterval(checkRoute, 500);
  window.addEventListener("popstate", checkRoute);
  checkRoute();
})();
