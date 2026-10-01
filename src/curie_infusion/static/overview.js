// Overview tab: stat tiles + one shared-time-axis chart (flags, infusion lanes, vitals, labs).
// Hand-rolled SVG (no dependencies: hospital networks are often offline). All labels go in via
// textContent. Exposes window.CurieOverview.render(root, data, opts).
(() => {
  const NS = "http://www.w3.org/2000/svg";
  const T = (s) => Date.parse(String(s).slice(0, 19) + "Z");
  const el = (tag, attrs = {}, parent) => {
    const e = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
    if (parent) parent.appendChild(e);
    return e;
  };
  const text = (parent, x, y, s, attrs = {}) => { const t = el("text", { x, y, ...attrs }, parent); t.textContent = s; return t; };
  const h = (tag, cls, s) => { const e = document.createElement(tag); if (cls) e.className = cls; if (s != null) e.textContent = s; return e; };
  const nf = (v) => v == null ? "—" : Math.abs(v) >= 100 ? Math.round(v).toString() : Math.abs(v) >= 10 ? v.toFixed(1).replace(/\.0$/, "") : (+v.toFixed(2)).toString();
  const pad = (n) => String(n).padStart(2, "0");
  const hhmm = (ms) => { const d = new Date(ms); return `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`; };
  const mdhm = (ms) => { const d = new Date(ms); return `${d.getUTCMonth() + 1}/${d.getUTCDate()} ${hhmm(ms)}`; };

  const PANELS = [
    { label: "Heart rate", unit: "bpm", h: 64, series: [["hr", "HR", 1]] },
    { label: "Blood pressure", unit: "mmHg", h: 96, series: [["sbp", "SBP", 1], ["map", "MAP", 2], ["dbp", "DBP", 3]] },
    { label: "SpO2", unit: "%", h: 52, series: [["spo2", "SpO2", 1]], max: 100 },
    { label: "Resp. rate", unit: "/min", h: 52, series: [["rr", "RR", 1]] },
    { label: "Temperature", unit: "°F", h: 52, series: [["temp", "Temp", 1]] },
  ];
  const STATUS = [
    ["do_not_infuse", "■", "Hold / do not start", "crit"],
    ["review", "▲", "Review", "warn"],
    ["insufficient_context", "○", "Cannot check", "muted"],
  ];
  const RANGES = [[12, "12 h"], [24, "24 h"], [72, "3 days"], [0, "Whole stay"]];
  const L = 168, R = 118, LANE = 22, GAP = 14;

  function tiles(d, opts) {
    const s = d.stats, v = s.vitals, row = h("div", "tiles");
    const last = d.flags[d.flags.length - 1] || {};
    const add = (label, value, unit, sub, spark, status) => {
      const t = h("div", "tile");
      t.append(h("div", "k", label));
      const val = h("div", "v");
      if (status) { const i = h("span", `ico ${status[0]}`, status[1]); i.setAttribute("aria-hidden", "true"); val.append(i); }
      val.append(document.createTextNode(value));
      if (unit) val.append(h("span", "u", ` ${unit}`));
      t.append(val);
      if (spark && spark.length > 1) t.append(sparkline(spark));
      t.append(h("div", "s", sub || ""));
      row.append(t);
    };
    const holds = last.do_not_infuse || 0, reviews = last.review || 0;
    add("Safety now", holds ? `${holds} hold` : reviews ? `${reviews} review` : "No flags", "",
      (last.items || []).slice(0, 2).join(" · ") || "rules checked at the pump clock", null,
      holds ? ["crit", "■"] : reviews ? ["warn", "▲"] : null);
    const at = (x) => x ? `at ${hhmm(T(x.at))}` : "no reading in 24 h";
    if (v.hr) add("Heart rate", nf(v.hr.value), "bpm", at(v.hr), v.hr.spark);
    if (v.sbp) add("Blood pressure", `${nf(v.sbp.value)}/${v.dbp ? nf(v.dbp.value) : "—"}`, "mmHg",
      `${v.map ? `MAP ${nf(v.map.value)} · ` : ""}${at(v.sbp)}`, v.map ? v.map.spark : v.sbp.spark);
    if (v.spo2) add("SpO2", nf(v.spo2.value), "%", at(v.spo2), v.spo2.spark);
    if (v.rr) add("Resp. rate", nf(v.rr.value), "/min", at(v.rr), v.rr.spark);
    if (v.temp) add("Temperature", nf(v.temp.value), "°F", at(v.temp), v.temp.spark);
    if (s.fluid_24h) add("Net fluid 24 h", `${s.fluid_24h.net > 0 ? "+" : ""}${s.fluid_24h.net}`, "mL",
      `in ${s.fluid_24h.intake} · out ${s.fluid_24h.output}`);
    const running = d.drips.filter((x) => x.current).length;
    add("Running infusions", String(running), "", d.drips.filter((x) => x.current).map((x) => x.label).slice(0, 3).join(", ") || "none");
    return row;
  }

  function sparkline(points) {
    const w = 120, ht = 26, svg = el("svg", { width: w, height: ht, viewBox: `0 0 ${w} ${ht}`, class: "spark", "aria-hidden": "true" });
    const xs = points.map((p) => T(p[0])), ys = points.map((p) => p[1]);
    const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
    const sx = (x) => 2 + ((x - x0) / (x1 - x0 || 1)) * (w - 6), sy = (y) => ht - 3 - ((y - y0) / (y1 - y0 || 1)) * (ht - 6);
    el("path", { d: points.map((p, i) => `${i ? "L" : "M"}${sx(xs[i]).toFixed(1)},${sy(p[1]).toFixed(1)}`).join(""), class: "line s1" }, svg);
    el("circle", { cx: sx(xs.at(-1)), cy: sy(ys.at(-1)), r: 2.5, class: "dot s1" }, svg);
    return svg;
  }

  function toolbar(d, opts) {
    const bar = h("div", "ov-tools");
    const seg = h("div", "seg");
    for (const [hours, label] of RANGES) {
      const b = h("button", d.hours === hours ? "on" : "", label);
      b.onclick = () => opts.onRange(hours);
      seg.append(b);
    }
    bar.append(h("span", "muted", "Range ending at the pump clock"), seg);
    const legend = h("div", "legend");
    for (const [, glyph, label, cls] of STATUS) {
      const i = h("span", "lg"); const g = h("span", `ico ${cls}`, glyph); g.setAttribute("aria-hidden", "true");
      i.append(g, document.createTextNode(label)); legend.append(i);
    }
    const ab = h("span", "lg"); const dot = h("span", "ico ser", "●"); dot.setAttribute("aria-hidden", "true");
    ab.append(dot, document.createTextNode("Lab flagged abnormal")); legend.append(ab);
    const tbl = h("button", "linkish", "View as table");
    tbl.onclick = () => opts.onTable();
    bar.append(legend, tbl);
    return bar;
  }

  function niceTicks(t0, t1, width) {
    const H = 3600e3, steps = [H, 2 * H, 3 * H, 6 * H, 12 * H, 24 * H, 48 * H, 7 * 24 * H, 14 * 24 * H];
    const want = Math.max(2, Math.floor(width / 90));
    const step = steps.find((s) => (t1 - t0) / s <= want) || steps.at(-1);
    const out = [];
    for (let t = Math.ceil(t0 / step) * step; t <= t1; t += step) out.push(t);
    return { ticks: out, step };
  }

  function chart(d, opts, width) {
    const t0 = T(d.start), t1 = T(d.end);
    const W = Math.max(520, width), plotW = W - L - R;
    const x = (t) => L + ((t - t0) / (t1 - t0 || 1)) * plotW;
    const xinv = (px) => t0 + ((px - L) / plotW) * (t1 - t0);
    const tol = Math.max(45 * 60e3, (t1 - t0) / 60);

    const lanes = d.drips;
    const bolusOther = d.boluses.filter((b) => !lanes.some((l) => l.itemid === b.itemid));
    const panels = PANELS.filter((p) => p.series.some(([k]) => (d.vitals[k] || []).length));
    let H = 26 + LANE + GAP;                                   // axis + flags
    const infH = (lanes.length + (bolusOther.length ? 1 : 0)) * LANE;
    H += infH + (infH ? GAP + 14 : 0);
    H += panels.reduce((a, p) => a + p.h + GAP + 14, 0);
    H += d.labs.length ? d.labs.length * LANE + 14 + GAP : 0;

    const svg = el("svg", { width: W, height: H, viewBox: `0 0 ${W} ${H}`, class: "ov", role: "img", tabindex: 0,
      "aria-label": "Timeline of safety flags, infusions, vital signs and labs; arrow keys move the crosshair, Enter moves the pump clock" });
    const g = el("g", {}, svg);

    // time axis
    const { ticks, step } = niceTicks(t0, t1, plotW);
    for (const t of ticks) {
      el("line", { x1: x(t), x2: x(t), y1: 20, y2: H, class: "grid" }, g);
      const dd = new Date(t);
      text(g, x(t), 14, step >= 24 * 3600e3 || (dd.getUTCHours() === 0) ? `${dd.getUTCMonth() + 1}/${dd.getUTCDate()}` : hhmm(t), { class: "tick", "text-anchor": "middle" });
    }
    text(g, W - R + 6, 14, "pump clock", { class: "tick" });
    let y = 26;

    const sectionTitle = (s) => { text(g, 8, y + 10, s, { class: "sec" }); y += 14; };
    const rowLabel = (s, yy, sub) => {
      const t = text(g, 8, yy, s.length > 22 ? s.slice(0, 21) + "…" : s, { class: "lab" });
      if (sub) { const u = el("tspan", { class: "unit" }, t); u.textContent = ` ${sub}`; }
    };

    // flags lane
    rowLabel("Safety flags", y + 15);
    const fl = d.flags;
    fl.forEach((f, i) => {
      const a = T(f.t), b = i + 1 < fl.length ? T(fl[i + 1].t) : t1;
      const s = STATUS.find(([k]) => f[k] > 0);
      if (!s) return;
      const x0 = i === fl.length - 1 ? x(a) - 3 : x(a), wpx = Math.max(6, x(b) - x0 - 2);
      el("rect", { x: x0, y: y + 3, width: wpx, height: LANE - 6, rx: 3, class: `flag ${s[3]}` }, g);
      if (wpx >= 12) text(g, x0 + wpx / 2, y + 15, s[1], { class: `glyph ${s[3]}`, "text-anchor": "middle" });
    });
    y += LANE + GAP;

    // infusion lanes
    if (infH) {
      sectionTitle("Infusions (bar height = rate vs. that drug's peak)");
      for (const lane of lanes) {
        rowLabel(lane.label, y + 15);
        el("line", { x1: L, x2: W - R, y1: y + LANE - 2, y2: y + LANE - 2, class: "base" }, g);
        for (const [s, e, rate] of lane.segments) {
          const a = Math.max(t0, T(s)), b = e ? Math.min(t1, T(e)) : t1;
          if (b <= t0) continue;
          const frac = rate == null || !lane.max_rate ? 0.45 : Math.max(0.18, rate / lane.max_rate);
          const hh = (LANE - 6) * frac;
          el("rect", { x: x(a) + 1, y: y + LANE - 2 - hh, width: Math.max(2, x(b) - x(a) - 2), height: hh, rx: 2,
            class: rate == null ? "seg additive" : "seg" }, g);
        }
        for (const b of d.boluses.filter((b) => b.itemid === lane.itemid)) diamond(g, x(T(b.t)), y + LANE / 2);
        if (lane.current) text(g, W - R + 6, y + 15, lane.current.rate != null ? `${nf(lane.current.rate)} ${lane.current.unit}` : "running", { class: "val" });
        y += LANE;
      }
      if (bolusOther.length) {
        rowLabel("Other boluses", y + 15);
        for (const b of bolusOther) diamond(g, x(T(b.t)), y + LANE / 2);
        y += LANE;
      }
      if (d.more_drips) text(g, L, y + 10, `+${d.more_drips} more infusions in the Infusions & vitals tab`, { class: "tick" });
      y += GAP;
    }

    // vitals panels
    const lineSeries = [];
    for (const p of panels) {
      sectionTitle(`${p.label} (${p.unit})`);
      const all = p.series.flatMap(([k]) => (d.vitals[k] || []).map((q) => q[1]));
      let lo = Math.min(...all), hi = Math.max(...all);
      const padv = (hi - lo || 1) * 0.12; lo -= padv; hi += padv;
      if (p.max) hi = Math.min(hi, p.max + 0.5);
      const sy = (v) => y + p.h - 4 - ((v - lo) / (hi - lo)) * (p.h - 8);
      el("line", { x1: L, x2: W - R, y1: y + p.h, y2: y + p.h, class: "base" }, g);
      text(g, L - 6, sy(hi) + 9, nf(hi), { class: "tick", "text-anchor": "end" });
      text(g, L - 6, sy(lo), nf(lo), { class: "tick", "text-anchor": "end" });
      const gapMs = Math.max(2 * 3600e3, (t1 - t0) / 40);
      const ends = [];
      p.series.forEach(([k, name, slot]) => {
        const pts = d.vitals[k] || [];
        if (!pts.length) return;
        let dstr = "", prev = null;
        for (const [ts, v] of pts) {
          const tt = T(ts);
          dstr += `${prev == null || tt - prev > gapMs ? "M" : "L"}${x(tt).toFixed(1)},${sy(v).toFixed(1)}`;
          prev = tt;
        }
        el("path", { d: dstr, class: `line s${slot}` }, g);
        if (pts.length < 40) for (const [ts, v] of pts) el("circle", { cx: x(T(ts)), cy: sy(v), r: 2, class: `dot s${slot}` }, g);
        const lastP = pts.at(-1);
        ends.push({ y: sy(lastP[1]), s: `${p.series.length > 1 ? name + " " : ""}${nf(lastP[1])}`, slot });
        lineSeries.push({ k, name, pts: pts.map(([ts, v]) => [T(ts), v]), unit: p.unit });
      });
      ends.sort((a, b) => a.y - b.y);
      for (let i = 1; i < ends.length; i++) ends[i].y = Math.max(ends[i].y, ends[i - 1].y + 12);
      for (const e of ends) {
        el("line", { x1: W - R + 4, x2: W - R + 14, y1: e.y - 4, y2: e.y - 4, class: `key s${e.slot}` }, g);
        text(g, W - R + 18, e.y, e.s, { class: "val" });
      }
      y += p.h + GAP;
    }

    // lab lanes
    if (d.labs.length) {
      sectionTitle("Key labs (shown once resulted)");
      for (const lab of d.labs) {
        rowLabel(lab.label, y + 15, lab.unit);
        el("line", { x1: L, x2: W - R, y1: y + LANE / 2, y2: y + LANE / 2, class: "grid" }, g);
        let lastX = -1e9;
        for (const [ts, v, ab] of lab.points) {
          const px = x(T(ts));
          el("circle", { cx: px, cy: y + LANE / 2, r: 4, class: ab ? "labdot ab" : "labdot" }, g);
          // the right margin carries the latest value, so skip labels that would run into it
          if (px - lastX >= 38 && px < W - R - 30) { text(g, px + 6, y + LANE / 2 - 4, nf(v), { class: ab ? "lv ab" : "lv" }); lastX = px; }
        }
        const lp = lab.points.at(-1);
        text(g, W - R + 6, y + 15, `${nf(lp[1])}${lp[2] ? " !" : ""}`, { class: lp[2] ? "val ab" : "val" });
        y += LANE;
      }
    }

    // pump clock + crosshair + hit layer
    el("line", { x1: x(t1), x2: x(t1), y1: 20, y2: H, class: "clock" }, g);
    const cross = el("line", { x1: 0, x2: 0, y1: 20, y2: H, class: "cross", visibility: "hidden" }, g);
    const hit = el("rect", { x: L, y: 20, width: plotW, height: H - 20, class: "hit" }, g);

    const tip = h("div", "ov-tip"); tip.hidden = true; tip.setAttribute("role", "status");
    const nearest = (pts, t) => {
      let best = null;
      for (const p of pts) if (Math.abs(p[0] - t) <= tol && (!best || Math.abs(p[0] - t) < Math.abs(best[0] - t))) best = p;
      return best;
    };
    let curT = t1;
    const show = (t, px, py) => {
      curT = t;
      cross.setAttribute("x1", x(t)); cross.setAttribute("x2", x(t)); cross.setAttribute("visibility", "visible");
      tip.replaceChildren();
      tip.append(h("div", "tt", mdhm(t)));
      const f = [...fl].reverse().find((q) => T(q.t) <= t);
      if (f && f.items.length) for (const it of f.items) {
        const s = STATUS.find(([, , , c]) => c === (it.startsWith("Hold") || it.startsWith("Do not") ? "crit" : it.startsWith("Review") ? "warn" : "muted"));
        const r = h("div", "tr"); const g2 = h("span", `ico ${s[3]}`, s[1]); g2.setAttribute("aria-hidden", "true");
        r.append(g2, h("b", null, it)); tip.append(r);
      }
      for (const s of lineSeries) {
        const p = nearest(s.pts, t);
        if (!p) continue;
        const r = h("div", "tr"); r.append(h("span", `k s${PANELS.flatMap((q) => q.series).find(([k]) => k === s.k)[2]}`), h("b", null, nf(p[1])), h("span", "muted", ` ${s.name} ${s.unit}`));
        tip.append(r);
      }
      for (const lane of lanes) {
        const seg = lane.segments.find(([s, e]) => T(s) <= t && (e ? T(e) : t1) >= t);
        if (!seg) continue;
        const r = h("div", "tr"); r.append(h("span", "k seg"), h("b", null, seg[2] != null ? `${nf(seg[2])} ${lane.unit || ""}` : "running"), h("span", "muted", ` ${lane.label}`));
        tip.append(r);
      }
      for (const b of d.boluses) if (Math.abs(T(b.t) - t) <= tol) {
        const r = h("div", "tr"); r.append(h("span", "k bol"), h("b", null, `${nf(b.amount)} ${b.unit}`), h("span", "muted", ` ${b.label} at ${hhmm(T(b.t))}`));
        tip.append(r);
      }
      for (const lab of d.labs) {
        const p = [...lab.points].reverse().find((q) => T(q[0]) <= t);
        if (!p || t - T(p[0]) > 24 * 3600e3) continue;
        const r = h("div", "tr"); r.append(h("span", p[2] ? "k labab" : "k lab"), h("b", null, `${nf(p[1])}${p[2] ? " !" : ""}`), h("span", "muted", ` ${lab.key} ${lab.unit} (${hhmm(T(p[0]))})`));
        tip.append(r);
      }
      tip.append(h("div", "hint", "Click to move the pump clock here"));
      tip.hidden = false;
      const box = svg.getBoundingClientRect(), tw = 250;
      tip.style.left = `${Math.min(box.width - tw - 8, Math.max(8, px + 14))}px`;
      tip.style.top = `${Math.max(4, py - 20)}px`;
    };
    const hide = () => { tip.hidden = true; cross.setAttribute("visibility", "hidden"); };
    const local = (e) => { const r = svg.getBoundingClientRect(); return [(e.clientX - r.left) * (W / r.width), e.clientY - r.top]; };
    hit.addEventListener("pointermove", (e) => { const [px, py] = local(e); show(xinv(px), px, py); });
    hit.addEventListener("pointerleave", hide);
    hit.addEventListener("click", (e) => { const [px] = local(e); opts.onSetClock(new Date(xinv(px))); });
    svg.addEventListener("keydown", (e) => {
      const stepMs = Math.max(3600e3, (t1 - t0) / 48);
      if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
        e.preventDefault(); e.stopPropagation();
        const nt = Math.min(t1, Math.max(t0, curT + (e.key === "ArrowLeft" ? -stepMs : stepMs)));
        show(nt, x(nt), 60);
      } else if (e.key === "Enter") { e.preventDefault(); opts.onSetClock(new Date(curT)); }
      else if (e.key === "Escape") hide();
    });
    svg.addEventListener("blur", hide);
    return [svg, tip];
  }

  function diamond(g, cx, cy) {
    el("path", { d: `M${cx},${cy - 5}L${cx + 5},${cy}L${cx},${cy + 5}L${cx - 5},${cy}Z`, class: "bolus" }, g);
  }

  function render(root, d, opts) {
    root.replaceChildren();
    root.append(tiles(d, opts), toolbar(d, opts));
    const wrap = h("div", "ov-wrap");
    root.append(wrap);
    const [svg, tip] = chart(d, opts, wrap.clientWidth || root.clientWidth || 900);
    wrap.append(svg, tip);
    if (!d.drips.length && !d.labs.length && !Object.values(d.vitals).some((v) => v.length))
      wrap.append(h("p", "empty", "Nothing charted in this range."));
  }

  window.CurieOverview = { render };
})();
