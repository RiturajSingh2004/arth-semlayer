"""Registry JSON API + Ryoku-themed technical Governance & Review UI (FastAPI + HTMX, zero build)."""
import json
from datetime import UTC, datetime
from html import escape as e

from fastapi import FastAPI, Form, Header, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from semlayer.compiler import SimpleCompiler
from semlayer.conflicts import apply_pick, apply_split, detect
from semlayer.core import KINDS, InvalidTransition, State
from semlayer.monitor import (
    feedback_summary,
    read_usage,
    recommend_materialized_views,
    run_canary,
)
from semlayer.registry import Registry
from semlayer.warehouse.bigquery import render_sql

from .service import Refusal, Service

HTMX = '<script src="https://unpkg.com/htmx.org@1.9.12"></script>'

STYLE = """<style>
:root {
  --bg-canvas: #0d0c0b;
  --bg-sidebar: #0a0908;
  --bg-surface: #141211;
  --bg-elevated: #1b1817;
  --bg-hover: #221e1c;
  --bg-accent-dim: #291913;
  --border: #221f1d;
  --border-strong: #302b28;
  --text-primary: #f5f4f2;
  --text-secondary: #a8a29e;
  --text-muted: #736c67;
  --accent: #e8652b;
  --accent-bright: #f2743d;
  --ok: #34d399;
  --ok-bg: rgba(52, 211, 153, 0.12);
  --warn: #fbbf24;
  --warn-bg: rgba(251, 191, 36, 0.12);
  --err: #f87171;
  --err-bg: rgba(248, 113, 113, 0.12);
  --sans: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
  --mono: "JetBrains Mono", "Cascadia Code", ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}
* { box-sizing: border-box; }
html { scroll-behavior: smooth; background: var(--bg-canvas); color: var(--text-primary); font-family: var(--sans); font-size: 14px; line-height: 1.55; }
body { margin: 0; background: var(--bg-canvas); color: var(--text-primary); }
a { color: inherit; text-decoration: none; }
code, pre, .mono { font-family: var(--mono); font-size: 12.5px; }
pre { margin: 6px 0 0; padding: 10px 12px; background: #090808; border: 1px solid var(--border); border-radius: 6px; overflow-x: auto; color: #e7e5e4; line-height: 1.45; }

/* Topbar & Rolling Brand */
.topbar { position: sticky; top: 0; z-index: 30; display: flex; align-items: center; justify-content: space-between; gap: 20px; padding: 0 28px; height: 60px; background: rgba(13, 12, 11, 0.92); backdrop-filter: blur(10px); border-bottom: 1px solid var(--border); }
.brand-wrap { display: flex; align-items: center; gap: 26px; height: 100%; }
.brand { font-size: 17.5px; font-weight: 700; letter-spacing: -0.02em; color: var(--text-primary); display: inline-flex; align-items: center; gap: 6px; white-space: nowrap; }
.brand-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--accent); box-shadow: 0 0 10px var(--accent); margin-right: 4px; flex-shrink: 0; }
.brand-roller { display: inline-block; height: 26px; line-height: 26px; overflow: hidden; vertical-align: bottom; text-align: center; }
.brand-reel { display: flex; flex-direction: column; align-items: center; will-change: transform; animation: brandRollFallback 7.2s cubic-bezier(0.22, 1, 0.36, 1) infinite; }
.brand-reel.js-driven { animation: none; }
.reel-word { display: flex; align-items: center; justify-content: center; width: 100%; height: 26px; line-height: 26px; text-align: center; white-space: nowrap; }
.reel-dhi { color: var(--accent-bright); }
@keyframes brandRollFallback {
  0%, 38% { transform: translateY(0); }
  48%, 86% { transform: translateY(-26px); }
  96%, 100% { transform: translateY(-52px); }
}
.top-tabs { display: inline-flex; align-items: center; gap: 22px; height: 100%; }
.top-tab { display: inline-flex; align-items: center; gap: 7px; height: 100%; font-size: 13px; font-weight: 600; color: var(--text-secondary); border-bottom: 2px solid transparent; padding: 0 2px; cursor: pointer; transition: color 0.15s, border-color 0.15s; }
.top-tab:hover { color: var(--text-primary); }
.top-tab.active { color: var(--text-primary); border-bottom-color: var(--accent); }
.top-meta { display: flex; align-items: center; gap: 12px; }
.search-pill { display: flex; align-items: center; gap: 10px; background: var(--bg-elevated); border: 1px solid var(--border-strong); border-radius: 999px; padding: 7px 14px; width: 340px; max-width: 42vw; transition: border-color 0.15s; }
.search-pill:focus-within { border-color: var(--accent); }
.search-pill input { flex: 1; background: transparent; border: none; outline: none; color: var(--text-primary); font-size: 13px; font-family: var(--sans); }
.search-pill input::placeholder { color: var(--text-muted); }
.kbd { font-family: var(--mono); font-size: 11px; color: var(--text-secondary); background: #100e0d; border: 1px solid var(--border-strong); border-radius: 4px; padding: 1px 6px; }

/* Shell */
.shell { display: grid; grid-template-columns: 260px minmax(0, 1fr); min-height: calc(100vh - 60px); }
.sidebar { background: var(--bg-sidebar); border-right: 1px solid var(--border); padding: 22px 16px 40px; position: sticky; top: 60px; height: calc(100vh - 60px); overflow-y: auto; }
.nav-group { margin-bottom: 20px; padding-bottom: 16px; border-bottom: 1px solid var(--border); }
.nav-group:last-child { border-bottom: none; }
.nav-title { font-size: 11.5px; font-weight: 700; color: var(--text-primary); letter-spacing: 0.02em; margin: 0 10px 8px; }
.nav-link { display: flex; align-items: center; justify-content: space-between; padding: 7px 11px; border-radius: 7px; color: var(--text-secondary); font-size: 13px; margin-bottom: 2px; transition: background 0.12s, color 0.12s; }
.nav-link:hover { background: var(--bg-surface); color: var(--text-primary); }
.nav-link.active { background: var(--bg-accent-dim); color: var(--accent-bright); font-weight: 500; }
.nav-count { font-family: var(--mono); font-size: 11px; padding: 1px 7px; border-radius: 999px; background: var(--bg-elevated); color: var(--text-secondary); border: 1px solid var(--border); }
.nav-link.active .nav-count { background: rgba(232, 101, 43, 0.2); color: var(--accent-bright); border-color: rgba(232, 101, 43, 0.35); }

/* Main content */
.content { padding: 34px 44px 72px; max-width: 1280px; }
.eyebrow { color: var(--accent); font-size: 12.5px; font-weight: 600; letter-spacing: 0.01em; margin-bottom: 6px; }
h1 { font-size: 30px; font-weight: 700; letter-spacing: -0.025em; line-height: 1.2; margin: 0 0 10px; color: var(--text-primary); }
.lead { font-size: 15px; color: var(--text-secondary); max-width: 820px; margin: 0 0 24px; line-height: 1.6; }
h2 { font-size: 20px; font-weight: 700; letter-spacing: -0.015em; margin: 38px 0 6px; color: var(--text-primary); display: flex; align-items: center; gap: 10px; }
.sec-sub { color: var(--text-secondary); font-size: 13.5px; margin: 0 0 16px; }

/* Stat strip */
.hero-card { background: radial-gradient(ellipse at 0% 0%, rgba(232, 101, 43, 0.11), transparent 55%), var(--bg-surface); border: 1px solid var(--border-strong); border-radius: 14px; padding: 22px 24px 20px; margin-bottom: 20px; box-shadow: 0 8px 30px rgba(0, 0, 0, 0.25); }
.hero-top { display: flex; flex-wrap: wrap; align-items: flex-start; justify-content: space-between; gap: 16px; }
.hero-actions { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.status-pill { display: inline-flex; align-items: center; gap: 7px; font-size: 12px; font-weight: 600; padding: 4px 11px; border-radius: 999px; border: 1px solid var(--border-strong); background: var(--bg-elevated); color: var(--text-primary); margin-bottom: 10px; }
.status-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--ok); box-shadow: 0 0 8px var(--ok); }
.status-dot.warn { background: var(--warn); box-shadow: 0 0 8px var(--warn); }
.status-dot.err { background: var(--err); box-shadow: 0 0 8px var(--err); }
.btn-ghost { display: inline-flex; align-items: center; gap: 6px; padding: 7px 12px; border-radius: 7px; border: 1px solid var(--border-strong); background: var(--bg-elevated); color: var(--text-primary); font-size: 12.5px; font-weight: 600; cursor: pointer; transition: all 0.14s; }
.btn-ghost:hover { background: var(--bg-hover); border-color: rgba(232, 101, 43, 0.45); color: var(--accent-bright); }
.ws-tabs { display: flex; flex-wrap: wrap; gap: 8px; margin: 0 0 22px; padding-bottom: 14px; border-bottom: 1px solid var(--border); }
.ws-tab { padding: 6px 13px; border-radius: 999px; font-size: 12.5px; font-weight: 600; color: var(--text-secondary); background: var(--bg-surface); border: 1px solid var(--border); cursor: pointer; transition: all 0.14s; }
.ws-tab:hover { color: var(--text-primary); border-color: var(--border-strong); }
.ws-tab.active { background: var(--bg-accent-dim); color: var(--accent-bright); border-color: rgba(232, 101, 43, 0.45); }
.sec-hidden { display: none !important; }
.stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(155px, 1fr)); gap: 12px; margin-bottom: 22px; }
.stat { display: block; background: var(--bg-surface); border: 1px solid var(--border); border-radius: 10px; padding: 14px 16px; transition: transform 0.15s, border-color 0.15s, background 0.15s; }
.stat:hover { transform: translateY(-2px); border-color: rgba(232, 101, 43, 0.45); background: var(--bg-elevated); }
.stat-k { font-size: 11px; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.05em; font-weight: 600; }
.stat-v { font-size: 23px; font-weight: 700; color: var(--text-primary); margin-top: 4px; font-family: var(--mono); }
.stat-v.accent { color: var(--accent-bright); }
.stat-v.ok { color: var(--ok); }
.stat-v.err { color: var(--err); }
.stat-sub { font-size: 11.5px; color: var(--text-secondary); margin-top: 3px; }
.preset-bar { display: flex; flex-wrap: wrap; align-items: center; gap: 7px; margin-bottom: 14px; padding: 11px 14px; background: var(--bg-surface); border: 1px solid var(--border); border-radius: 10px; }
.preset-label { font-size: 11.5px; font-weight: 700; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.04em; margin-right: 4px; }
.preset-btn { font-size: 12px; padding: 5px 10px; border-radius: 999px; background: var(--bg-elevated); border: 1px solid var(--border-strong); color: var(--text-secondary); font-weight: 500; transition: all 0.14s; }
.preset-btn:hover { color: var(--text-primary); border-color: var(--accent); background: var(--bg-accent-dim); }
.preset-btn.danger-chip:hover { color: var(--err); border-color: var(--err); background: var(--err-bg); }
.adv-box { margin: 10px 0 12px; padding: 9px 12px; background: #100e0d; border: 1px solid var(--border); border-radius: 8px; }
.adv-box summary { color: var(--text-secondary); font-size: 12px; font-weight: 600; }
.adv-box[open] summary { margin-bottom: 10px; color: var(--accent-bright); }
.empty-card { background: var(--bg-surface); border: 1px dashed var(--border-strong); border-radius: 10px; padding: 20px 22px; color: var(--text-secondary); display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
.empty-card strong { color: var(--text-primary); }
.pre-wrap { position: relative; }
.copy-btn { position: absolute; top: 6px; right: 6px; font-size: 11px; padding: 3px 8px; border-radius: 5px; background: rgba(27, 24, 23, 0.92); border: 1px solid var(--border-strong); color: var(--text-secondary); opacity: 0.78; }
.copy-btn:hover { opacity: 1; color: var(--accent-bright); border-color: var(--accent); }
.toast { position: fixed; bottom: 22px; right: 24px; z-index: 60; padding: 10px 16px; border-radius: 8px; background: var(--bg-elevated); border: 1px solid rgba(232, 101, 43, 0.45); color: var(--text-primary); font-size: 13px; font-weight: 600; box-shadow: 0 10px 30px rgba(0, 0, 0, 0.45); opacity: 0; transform: translateY(10px); pointer-events: none; transition: all 0.2s; }
.toast.show { opacity: 1; transform: translateY(0); }

/* Badges & Pills */
.badge { display: inline-flex; align-items: center; gap: 5px; font-family: var(--mono); font-size: 11px; font-weight: 600; padding: 2px 8px; border-radius: 5px; border: 1px solid var(--border-strong); background: var(--bg-elevated); color: var(--text-secondary); white-space: nowrap; }
.badge-orange { background: var(--bg-accent-dim); color: var(--accent-bright); border-color: rgba(232, 101, 43, 0.35); }
.badge-ok { background: var(--ok-bg); color: var(--ok); border-color: rgba(52, 211, 153, 0.3); }
.badge-warn { background: var(--warn-bg); color: var(--warn); border-color: rgba(251, 191, 36, 0.3); }
.badge-err { background: var(--err-bg); color: var(--err); border-color: rgba(248, 113, 113, 0.3); }

/* Tables */
.table-wrap { background: var(--bg-surface); border: 1px solid var(--border); border-radius: 10px; overflow: hidden; }
table { width: 100%; border-collapse: collapse; text-align: left; }
th { font-size: 12px; font-weight: 600; color: var(--text-primary); padding: 11px 14px; border-bottom: 1px solid var(--border-strong); background: #110f0e; white-space: nowrap; }
td { padding: 13px 14px; border-bottom: 1px solid var(--border); vertical-align: top; color: var(--text-secondary); font-size: 13px; }
tr:last-child td { border-bottom: none; }
tr:hover td { background: rgba(255, 255, 255, 0.015); }
.def-name { color: var(--text-primary); font-weight: 600; font-size: 13.5px; }
.sql-chip { display: inline-block; padding: 3px 7px; background: #090808; border: 1px solid var(--border-strong); border-radius: 5px; color: #f3f4f6; font-family: var(--mono); font-size: 12px; word-break: break-word; }
.desc-text { color: var(--text-primary); margin-top: 6px; line-height: 1.45; }
.meta-row { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 6px; font-size: 11.5px; color: var(--text-muted); }

/* Details & Diffs */
details { margin-top: 4px; }
summary { cursor: pointer; color: var(--accent-bright); font-size: 12px; font-weight: 500; user-select: none; }
summary:hover { text-decoration: underline; }
.diff-add { color: var(--ok); }
.diff-del { color: var(--err); }

/* Forms, Inputs & Buttons */
input, select { background: #0b0a09; border: 1px solid var(--border-strong); color: var(--text-primary); border-radius: 6px; padding: 6px 9px; font-size: 12.5px; font-family: var(--sans); outline: none; }
input:focus, select:focus { border-color: var(--accent); }
button { cursor: pointer; font-family: var(--sans); font-size: 12.5px; font-weight: 600; border-radius: 6px; padding: 6px 11px; border: 1px solid var(--border-strong); background: var(--bg-elevated); color: var(--text-primary); transition: background 0.12s, border-color 0.12s; }
button:hover { background: var(--bg-hover); border-color: #423b37; }
.btn-primary { background: var(--accent); border-color: var(--accent); color: #0d0c0b; font-weight: 700; }
.btn-primary:hover { background: var(--accent-bright); border-color: var(--accent-bright); }
.btn-danger { background: transparent; border-color: rgba(248, 113, 113, 0.35); color: var(--err); }
.btn-danger:hover { background: var(--err-bg); border-color: var(--err); }
.act-stack { display: flex; flex-direction: column; gap: 6px; min-width: 195px; }
.act-inline { display: flex; gap: 6px; align-items: center; }
.act-inline input { width: 115px; flex: 1; }
.err-msg { display: block; margin-top: 4px; color: var(--err); font-size: 12px; font-weight: 600; }

/* Cards & Grids */
.card-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(340px, 1fr)); gap: 14px; }
.card { background: var(--bg-surface); border: 1px solid var(--border); border-radius: 10px; padding: 16px 18px; }
.card-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-bottom: 10px; }
.card-title { font-weight: 700; color: var(--text-primary); font-size: 14.5px; }
.split-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin: 8px 0; }
.playground-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(380px, 1fr)); gap: 16px; }
.form-row { display: grid; grid-template-columns: 110px 1fr; align-items: center; gap: 8px; margin-bottom: 8px; font-size: 12.5px; color: var(--text-secondary); }
.empty-note { color: var(--text-muted); font-style: italic; padding: 6px 0; }

/* System Architecture Canvas */
.tab-view.hidden { display: none; }
.arch-toolbar { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 14px; }
.arch-pills { display: flex; flex-wrap: wrap; gap: 8px; }
.arch-pill { font-size: 12px; padding: 5px 12px; border-radius: 999px; background: var(--bg-surface); border: 1px solid var(--border-strong); color: var(--text-secondary); cursor: pointer; font-weight: 600; transition: all 0.15s; }
.arch-pill:hover { color: var(--text-primary); border-color: #4a423d; }
.arch-pill.active { background: var(--bg-accent-dim); color: var(--accent-bright); border-color: rgba(232, 101, 43, 0.45); }
.arch-legend { display: flex; flex-wrap: wrap; gap: 14px; font-size: 11.5px; color: var(--text-secondary); font-family: var(--mono); }
.leg-dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 5px; vertical-align: middle; }
.arch-stage { background: radial-gradient(circle at 50% 42%, rgba(232, 101, 43, 0.06), transparent 55%), #0a0908; border: 1px solid var(--border-strong); border-radius: 12px; padding: 14px; overflow-x: auto; box-shadow: inset 0 1px 0 rgba(255,255,255,0.03); }
.arch-svg { width: 100%; min-width: 960px; height: auto; display: block; }
.arch-flow { fill: none; stroke-width: 1.75; stroke-dasharray: 6 6; animation: archDash 18s linear infinite; }
.arch-flow-fast { fill: none; stroke-width: 2; stroke-dasharray: 7 5; animation: archDash 10s linear infinite; }
@keyframes archDash { to { stroke-dashoffset: -240; } }
.arch-pulse-ring { transform-box: fill-box; transform-origin: center; animation: gatePulse 2.8s ease-in-out infinite; }
@keyframes gatePulse {
  0%, 100% { opacity: 0.32; transform: scale(1); }
  50% { opacity: 0.75; transform: scale(1.03); }
}
.arch-node { cursor: pointer; transition: opacity 0.25s, filter 0.2s; }
.arch-node:hover { filter: brightness(1.16); }
.arch-dimmed { opacity: 0.18 !important; transition: opacity 0.25s; }
.arch-inspector { margin-top: 12px; background: var(--bg-surface); border: 1px solid var(--border-strong); border-left: 3px solid var(--accent); border-radius: 8px; padding: 12px 16px; display: flex; flex-wrap: wrap; align-items: baseline; justify-content: space-between; gap: 10px; }
.arch-ins-title { font-weight: 700; color: var(--text-primary); font-size: 13.5px; }
.arch-ins-detail { color: var(--text-secondary); font-size: 12.5px; flex: 1; min-width: 260px; }

@media (max-width: 960px) {
  .shell { grid-template-columns: 1fr; }
  .sidebar { position: static; height: auto; border-right: none; border-bottom: 1px solid var(--border); }
  .content { padding: 22px 18px 48px; }
}
</style>"""

SCRIPT = """<script>
document.addEventListener("DOMContentLoaded", () => {
  /* 1. Upward rolling wordmark: Semantic -> Dhī -> Semantic (centered in slot) */
  const roller = document.getElementById("brand-roller");
  const reel = document.getElementById("brand-reel");
  if (roller && reel) {
    const words = reel.querySelectorAll(".reel-word");
    if (words.length >= 3) {
      reel.classList.add("js-driven");
      let idx = 0;
      setInterval(() => {
        idx += 1;
        reel.style.transition = "transform 0.55s cubic-bezier(0.22, 1, 0.36, 1)";
        reel.style.transform = `translateY(-${idx * 26}px)`;
        if (idx === 2) {
          setTimeout(() => {
            reel.style.transition = "none";
            reel.style.transform = "translateY(0)";
            idx = 0;
          }, 580);
        }
      }, 3200);
    }
  }

  /* 2. Top-level tab switcher (Governance Console <-> System) */
  const tabLinks = document.querySelectorAll("[data-tab-btn]");
  const views = {
    governance: document.getElementById("view-governance"),
    system: document.getElementById("view-system"),
  };
  function activateTab(name, pushHash) {
    if (!views[name]) return;
    Object.entries(views).forEach(([k, el]) => {
      if (el) el.classList.toggle("hidden", k !== name);
    });
    tabLinks.forEach((btn) => btn.classList.toggle("active", btn.dataset.tabBtn === name));
    if (pushHash) {
      const url = new URL(window.location.href);
      url.searchParams.set("tab", name);
      window.history.replaceState({}, "", url);
    }
  }
  tabLinks.forEach((btn) => {
    btn.addEventListener("click", (ev) => {
      if (ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.altKey) return;
      ev.preventDefault();
      activateTab(btn.dataset.tabBtn, true);
    });
  });
  if (window.location.hash === "#system") activateTab("system", false);

  /* 3. Ctrl+K global filter */
  const input = document.getElementById("global-search");
  document.addEventListener("keydown", (ev) => {
    if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === "k") {
      ev.preventDefault();
      if (input) { input.focus(); input.select(); }
    }
  });
  if (input) {
    input.addEventListener("input", () => {
      const q = input.value.trim().toLowerCase();
      document.querySelectorAll("[data-filterable]").forEach((el) => {
        el.style.display = (!q || el.textContent.toLowerCase().includes(q)) ? "" : "none";
      });
    });
  }

  /* 4. Sidebar active pill highlight */
  document.querySelectorAll(".sidebar").forEach((sb) => {
    const links = sb.querySelectorAll(".nav-link");
    links.forEach((lnk) => {
      lnk.addEventListener("click", () => {
        links.forEach((x) => x.classList.remove("active"));
        lnk.classList.add("active");
      });
    });
  });

  /* 5. Interactive System Architecture stack filter & node inspector */
  const pills = document.querySelectorAll("[data-arch-filter]");
  const archItems = document.querySelectorAll("[data-stack]");
  pills.forEach((pill) => {
    pill.addEventListener("click", () => {
      const mode = pill.dataset.archFilter;
      pills.forEach((p) => p.classList.toggle("active", p === pill));
      archItems.forEach((el) => {
        const stacks = (el.dataset.stack || "").split(" ");
        const show = mode === "all" || stacks.includes("core") || stacks.includes(mode);
        el.classList.toggle("arch-dimmed", !show);
      });
    });
  });

  const insTitle = document.getElementById("arch-ins-title");
  const insDetail = document.getElementById("arch-ins-detail");
  const insAdr = document.getElementById("arch-ins-adr");
  document.querySelectorAll(".arch-node[data-title]").forEach((node) => {
    const update = () => {
      if (insTitle) insTitle.textContent = node.dataset.title || "";
      if (insDetail) insDetail.textContent = node.dataset.detail || "";
      if (insAdr) insAdr.textContent = node.dataset.adr || "";
    };
    node.addEventListener("mouseenter", update);
    node.addEventListener("click", update);
  });

  /* 6. Workspace section filter tabs + sidebar sync */
  const wsTabs = document.querySelectorAll("[data-ws-tab]");
  const wsSections = document.querySelectorAll("section[data-ws-group]");
  function setWsTab(group) {
    wsTabs.forEach((b) => b.classList.toggle("active", b.dataset.wsTab === group));
    wsSections.forEach((sec) => {
      const groups = (sec.dataset.wsGroup || "").split(" ");
      sec.classList.toggle("sec-hidden", group !== "all" && !groups.includes(group));
    });
  }
  wsTabs.forEach((btn) => btn.addEventListener("click", () => setWsTab(btn.dataset.wsTab)));
  document.querySelectorAll("#view-governance .sidebar .nav-link, .stat[href^='#'], .btn-ghost[href^='#']").forEach((lnk) => {
    lnk.addEventListener("click", () => setWsTab("all"));
  });

  /* 7. One-click Playground Presets */
  document.querySelectorAll("[data-preset]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const cfg = JSON.parse(btn.dataset.preset || "{}");
      if (cfg.kind === "traverse") {
        const f = document.getElementById("form-traverse");
        if (!f) return;
        ["start", "path", "as_role", "filters"].forEach((k) => {
          const el = f.querySelector(`[name="${k}"]`);
          if (el && !el.disabled && cfg[k] !== undefined) el.value = cfg[k];
        });
        f.requestSubmit();
      } else {
        const f = document.getElementById("form-query");
        if (!f) return;
        ["metric", "as_role", "dims", "filters", "time_grain", "time_range", "order_by", "limit", "entity_keys"].forEach((k) => {
          const el = f.querySelector(`[name="${k}"]`);
          if (el && !el.disabled) el.value = cfg[k] || "";
        });
        const adv = document.getElementById("query-adv");
        if (adv && (cfg.time_grain || cfg.time_range || cfg.order_by || cfg.limit)) adv.open = true;
        f.requestSubmit();
      }
      const out = document.getElementById("runner-output");
      if (out) out.scrollIntoView({ behavior: "smooth", block: "nearest" });
    });
  });

  /* 8. Copy-to-clipboard buttons on SQL/DDL <pre> blocks + HTMX toast */
  function attachCopyButtons(root) {
    (root || document).querySelectorAll("pre").forEach((pre) => {
      if (pre.parentElement && pre.parentElement.classList.contains("pre-wrap")) return;
      const wrap = document.createElement("div");
      wrap.className = "pre-wrap";
      pre.parentNode.insertBefore(wrap, pre);
      wrap.appendChild(pre);
      const b = document.createElement("button");
      b.type = "button";
      b.className = "copy-btn";
      b.textContent = "Copy";
      b.addEventListener("click", async () => {
        try {
          await navigator.clipboard.writeText(pre.textContent || "");
          b.textContent = "Copied!";
          setTimeout(() => { b.textContent = "Copy"; }, 1400);
        } catch (_) {}
      });
      wrap.appendChild(b);
    });
  }
  attachCopyButtons(document);

  const toast = document.getElementById("toast");
  function showToast(msg) {
    if (!toast) return;
    toast.textContent = msg;
    toast.classList.add("show");
    clearTimeout(toast._t);
    toast._t = setTimeout(() => toast.classList.remove("show"), 2400);
  }
  document.body.addEventListener("htmx:afterSwap", (ev) => {
    attachCopyButtons(ev.detail.target);
    if (ev.detail.target && ev.detail.target.id === "runner-output") {
      showToast("Executed governed request");
    } else {
      showToast("Governance state updated");
    }
  });
});
</script>"""


def _summary(n) -> str:
    if n.kind == "metric":
        if n.metric_type != "simple":
            return f"[{n.metric_type.upper()}] {n.expression}"
        w = f" WHERE {' AND '.join(n.filters)}" if n.filters else ""
        return f"{n.expression} FROM {n.table}{w}"
    if n.kind == "entity":
        return f"TABLE {n.table} (PK: {n.key})"
    if n.kind == "edge":
        return f"{n.src}.{n.src_key} -[{n.rel}]-> {n.dst}.{n.dst_key}"
    if n.kind == "rule":
        rf = f" WHERE {n.row_filter}" if n.row_filter else ""
        dd = f" deny_dims={n.deny_dims}" if n.deny_dims else ""
        return f"{n.effect.upper()} role={n.role} metric={n.metric}{rf}{dd}"
    return n.model_dump_json()


def _format_diff(diff_text: str) -> str:
    if not diff_text:
        return ""
    lines = []
    for ln in diff_text.splitlines():
        esc = e(ln)
        if ln.startswith("+") and not ln.startswith("+++"):
            lines.append(f'<span class="diff-add">{esc}</span>')
        elif ln.startswith("-") and not ln.startswith("---"):
            lines.append(f'<span class="diff-del">{esc}</span>')
        else:
            lines.append(esc)
    return f"<pre>{chr(10).join(lines)}</pre>"


def _row(n, msg: str = "", diff_text: str = "") -> str:
    ev, tag = n.evidence, "revision" if n.version else "draft"
    tag_cls = "badge-warn" if n.version else "badge-orange"
    act = f"/certify/{e(n.kind)}/{e(n.name)}"
    rej = f"/reject/{e(n.kind)}/{e(n.name)}"
    desc = f'<div class="desc-text"><i>{e(n.description)}</i></div>' if n.description else ""
    expl = f'<div class="meta-row"><span>{e(ev.explanation)}</span></div>' if ev.explanation else ""
    meta_bits = []
    if n.kind == "metric":
        if n.dimensions:
            meta_bits.append(f"dims: {', '.join(n.dimensions)}")
        if n.entity:
            meta_bits.append(f"entity: {n.entity} ({n.entity_key})")
    if ev.source:
        meta_bits.append(f"source: {ev.source}")
    if ev.users:
        meta_bits.append(f"users: {', '.join(ev.users)}")
    meta_html = f'<div class="meta-row">{"".join(f"<span class=badge>{e(b)}</span>" for b in meta_bits)}</div>' if meta_bits else ""
    q_block = f"<pre>{e(chr(10).join(ev.queries))}</pre>" if ev.queries else ""
    lin = e(", ".join(ev.lineage)) if ev.lineage else "-"
    diff_html = _format_diff(diff_text)
    err_html = f'<strong class="err-msg">{e(msg)}</strong>' if msg else ""
    ver_note = f" v{n.version}→v{n.version + 1}" if n.version else ""
    return (
        f'<tr id="{e(n.kind)}-{e(n.name)}" data-filterable>'
        f'<td><span class="badge">{e(n.kind)}</span> <b class="def-name">{e(n.name)}</b> '
        f'<span class="badge {tag_cls}">({tag}){e(ver_note)}</span></td>'
        f'<td><code class="sql-chip">{e(_summary(n))}</code>{desc}{expl}{meta_html}</td>'
        f'<td class="mono">{ev.usage}</td>'
        f'<td><details><summary>inspect lineage &amp; SQL ({len(ev.queries)})</summary>'
        f'{q_block}<div class="meta-row"><span>lineage: <code>{lin}</code></span></div>{diff_html}</details></td>'
        f'<td><div class="act-stack">'
        f'<form class="act-inline" method="post" action="{act}" hx-post="{act}" '
        f'hx-target="closest tr" hx-swap="outerHTML">'
        f'<input name="owner" placeholder="owner (required)" value="{e(n.owner or ev.suggested_owner or "")}">'
        f'<button class="btn-primary" type="submit">certify</button></form>'
        f'<form method="post" action="{rej}" hx-post="{rej}" hx-target="closest tr" hx-swap="outerHTML">'
        f'<button class="btn-danger" type="submit">reject</button></form>'
        f"{err_html}</div></td></tr>"
    )


def _conflict_card(c) -> str:
    variants_str = ", ".join(c.variants)
    diffs_html = "".join(f"<div><code>{e(d)}</code></div>" for d in c.diffs)
    picks = "".join(
        f'<form method="post" action="/conflicts/pick" style="display:inline-block;margin-right:6px;">'
        f'<input type="hidden" name="winner" value="{e(v)}">'
        f'<button type="submit">pick <code>{e(v)}</code> ({c.usage.get(v, 0)})</button></form>'
        for v in c.variants
    )
    split_html = ""
    if c.kind == "variant":
        inputs = "".join(
            f'<div class="act-inline"><code style="min-width:110px">{e(v)}</code>'
            f'<input name="rename_{e(v)}" value="{e(v)}" placeholder="distinct_name"></div>'
            for v in c.variants
        )
        split_html = (
            f'<form method="post" action="/conflicts/split" style="margin-top:10px;padding-top:10px;border-top:1px solid var(--border)">'
            f'<input type="hidden" name="concept" value="{e(c.concept)}">'
            f'<div class="meta-row" style="margin-bottom:6px"><span>Or split variants into distinct certified concepts:</span></div>'
            f'<div class="split-grid">{inputs}</div>'
            f'<button class="btn-primary" type="submit">split variants</button></form>'
        )
    return (
        f'<div class="card" data-filterable>'
        f'<div class="card-head"><span class="card-title"><b>{e(c.concept)}</b> ({e(c.kind)}): '
        f'{e(variants_str)}</span><span class="badge badge-warn">{e(c.kind)}</span></div>'
        f'<div style="margin-bottom:10px">{diffs_html}</div>'
        f"<div>{picks}</div>{split_html}</div>"
    )


def _compiled_preview(m, by_name: dict, project: str = "your-gcp-project",
                      dataset: str = "jaffle_shop") -> tuple[str, str]:
    dims = m.dimensions[:1]
    if m.metric_type == "simple":
        duck_c = SimpleCompiler(dialect="duckdb").compile(m, dims)
        bq_c = SimpleCompiler(dialect="bigquery").compile(m, dims)
    else:
        duck_c = SimpleCompiler(dialect="duckdb").compile_derived(m, by_name, dims)
        bq_c = SimpleCompiler(dialect="bigquery").compile_derived(m, by_name, dims)
    try:
        bq_sql = render_sql(bq_c.sql, bq_c.params, project, dataset)
    except Exception:
        bq_sql = bq_c.sql
    return duck_c.sql, bq_sql


def _render_query_result(res: dict | None, err: str = "") -> str:
    if err:
        return f'<div class="card" style="border-color:rgba(248,113,113,0.4)"><span class="badge badge-err">REFUSED</span> <strong style="color:var(--err);margin-left:6px">{e(err)}</strong></div>'
    if not res:
        return ""
    if "keys" in res:
        preview = ", ".join(map(str, res["keys"][:40])) + (" ..." if len(res["keys"]) > 40 else "")
        return (
            f'<div class="card"><div class="card-head"><span class="card-title">Graph traversal · '
            f'<code>{e(res["entity"])}</code></span><span class="badge badge-ok">{res["count"]} keys</span></div>'
            f'<pre>[{e(preview)}]</pre></div>'
        )
    cols_th = "".join(f"<th>{e(str(c))}</th>" for c in res["columns"])
    rows_tr = "".join(
        "<tr>" + "".join(f'<td class="mono">{e(str(v))}</td>' for v in r) + "</tr>"
        for r in res["rows"][:25]
    ) or f'<tr><td colspan="{max(1, len(res["columns"]))}"><i>0 rows</i></td></tr>'
    return (
        f'<div class="card"><div class="card-head">'
        f'<span class="card-title"><code>{e(res["metric"])}</code> '
        f'<span class="badge badge-ok">v{res["version"]}</span> '
        f'<span class="badge">owner: {e(res["owner"] or "-")}</span></span>'
        f'<span class="mono" style="color:var(--text-muted)">{res["row_count"]} row(s)</span></div>'
        f'<pre>{e(res["sql"])}</pre>'
        f'<div class="table-wrap" style="margin-top:10px"><table><tr>{cols_th}</tr>{rows_tr}</table></div></div>'
    )


def _clip(text: str, limit: int = 38) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _system_view(wh_dialect: str, bq_proj: str, bq_ds: str, cert_count: int,
                 rule_count: int, edge_count: int, hidden: bool) -> str:
    cls = "shell tab-view hidden" if hidden else "shell tab-view"
    active_mode = _clip(f"BigQuery MCP · {bq_ds}", 36) if wh_dialect == "bigquery" else "DuckDB · local read-only"
    bq_target = _clip(f"{bq_proj}.{bq_ds}", 38)
    return (
        f"<div id='view-system' class='{cls}'>"
        f"<aside class='sidebar'>"
        f"<div class='nav-group'><div class='nav-title'>System Architecture</div>"
        f"<a class='nav-link active' href='#sys-diagram'><span>Animated topology</span><span class='nav-count'>Dual</span></a>"
        f"<a class='nav-link' href='#sys-planes'><span>Execution planes</span><span class='nav-count'>4</span></a>"
        f"<a class='nav-link' href='#sys-matrix'><span>GCP vs Local matrix</span></a>"
        f"<a class='nav-link' href='#sys-invariants'><span>Security invariants</span><span class='nav-count'>8</span></a></div>"
        f"<div class='nav-group'><div class='nav-title'>Active Runtime</div>"
        f"<div style='padding:6px 11px;font-size:12px;color:var(--text-secondary)'>"
        f"<div>Warehouse: <code style='color:var(--accent-bright)'>{e(wh_dialect)}</code></div>"
        f"<div style='margin-top:4px'>Certified metrics: <code>{cert_count}</code></div>"
        f"<div style='margin-top:4px'>FK graph edges: <code>{edge_count}</code></div>"
        f"<div style='margin-top:4px'>Policy rules: <code>{rule_count}</code></div>"
        f"</div></div></aside>"
        f"<main class='content'>"
        f"<div class='eyebrow'>System Topology · GCP Cloud + Local Dual Stack</div>"
        f"<h1>ARTH: Semantic / Dhī Layer Architecture</h1>"
        f"<p class='lead'>Agents never touch raw tables or warehouse credentials. Every request passes through "
        f"human-certified YAML definitions, default-deny RBAC &amp; entity-graph gates, and <code>sqlglot</code> AST "
        f"compilation before executing read-only on Google's managed BigQuery MCP server or local DuckDB.</p>"
        f"<section id='sys-diagram'>"
        f"<div class='arch-toolbar'>"
        f"<div class='arch-pills'>"
        f"<button type='button' class='arch-pill active' data-arch-filter='all'>Dual Stack (GCP + Local)</button>"
        f"<button type='button' class='arch-pill' data-arch-filter='gcp'>GCP Cloud Path (Gemini + BigQuery MCP)</button>"
        f"<button type='button' class='arch-pill' data-arch-filter='local'>Local / Offline Path (Ollama + DuckDB)</button>"
        f"<button type='button' class='arch-pill' data-arch-filter='loop'>Governance &amp; Drift Loop</button>"
        f"</div>"
        f"<div class='arch-legend'>"
        f"<span><i class='leg-dot' style='background:#e8652b'></i>Core Governance</span>"
        f"<span><i class='leg-dot' style='background:#38bdf8'></i>GCP Cloud (AI Studio / BQ MCP)</span>"
        f"<span><i class='leg-dot' style='background:#34d399'></i>Local Engine (Ollama / DuckDB)</span>"
        f"<span><i class='leg-dot' style='background:#fbbf24'></i>Drift &amp; Feedback</span>"
        f"</div></div>"
        f"<div class='arch-stage'>"
        f"<svg class='arch-svg' viewBox='0 0 1120 610' xmlns='http://www.w3.org/2000/svg' role='img' aria-label='ARTH Semantic Layer Architecture'>"
        f"<defs>"
        f"<pattern id='arch-grid' width='24' height='24' patternUnits='userSpaceOnUse'>"
        f"<circle cx='2' cy='2' r='1' fill='#24201d'/></pattern>"
        f"<marker id='arr-orange' viewBox='0 0 10 10' refX='8' refY='5' markerWidth='6' markerHeight='6' orient='auto-start-reverse'>"
        f"<path d='M 0 1 L 10 5 L 0 9 z' fill='#e8652b'/></marker>"
        f"<marker id='arr-gcp' viewBox='0 0 10 10' refX='8' refY='5' markerWidth='6' markerHeight='6' orient='auto-start-reverse'>"
        f"<path d='M 0 1 L 10 5 L 0 9 z' fill='#38bdf8'/></marker>"
        f"<marker id='arr-local' viewBox='0 0 10 10' refX='8' refY='5' markerWidth='6' markerHeight='6' orient='auto-start-reverse'>"
        f"<path d='M 0 1 L 10 5 L 0 9 z' fill='#34d399'/></marker>"
        f"<marker id='arr-warn' viewBox='0 0 10 10' refX='8' refY='5' markerWidth='6' markerHeight='6' orient='auto-start-reverse'>"
        f"<path d='M 0 1 L 10 5 L 0 9 z' fill='#fbbf24'/></marker>"
        f"</defs>"
        f"<rect width='1120' height='610' rx='8' fill='url(#arch-grid)'/>"

        # Plane boundary containers
        f"<rect x='16' y='16' width='318' height='510' rx='10' fill='#110f0e' fill-opacity='0.65' stroke='#262220' stroke-dasharray='4 4'/>"
        f"<text x='30' y='38' fill='#736c67' font-size='11' font-weight='700' font-family='monospace'>PLANE 1 · INGEST, DRAFT &amp; LLM WORDING</text>"

        f"<rect x='354' y='16' width='372' height='510' rx='10' fill='#141110' fill-opacity='0.8' stroke='#3a241b'/>"
        f"<text x='368' y='38' fill='#f2743d' font-size='11' font-weight='700' font-family='monospace'>PLANE 2 · SEMLAYER CERTIFIED BOUNDARY (LOCAL)</text>"

        f"<g data-stack='gcp'>"
        f"<rect x='746' y='16' width='358' height='278' rx='10' fill='#0c141c' fill-opacity='0.72' stroke='#1e3a52' stroke-dasharray='5 4'/>"
        f"<text x='760' y='38' fill='#38bdf8' font-size='11' font-weight='700' font-family='monospace'>PLANE 3A · GOOGLE CLOUD (BIGQUERY OVER MCP)</text></g>"

        f"<g data-stack='local'>"
        f"<rect x='746' y='310' width='358' height='216' rx='10' fill='#0c1612' fill-opacity='0.72' stroke='#1a3d2f' stroke-dasharray='5 4'/>"
        f"<text x='760' y='332' fill='#34d399' font-size='11' font-weight='700' font-family='monospace'>PLANE 3B · LOCAL WAREHOUSE (DEFAULT / CI)</text></g>"

        # Animated connector paths
        # Ingest -> Draft -> Gate -> Registry -> Service
        f"<path id='p-ingest' data-stack='core loop' class='arch-flow' d='M 175 112 L 175 148' stroke='#e8652b' marker-end='url(#arr-orange)'/>"
        f"<path id='p-draft-gate' data-stack='core loop' class='arch-flow-fast' d='M 316 190 L 372 115' stroke='#e8652b' marker-end='url(#arr-orange)'/>"
        f"<path id='p-gemini-draft' data-stack='gcp' class='arch-flow-fast' d='M 175 300 L 175 234' stroke='#38bdf8' marker-end='url(#arr-gcp)'/>"
        f"<path id='p-ollama-draft' data-stack='local' class='arch-flow' d='M 175 416 L 175 380' stroke='#34d399' stroke-dasharray='4 5'/>"
        f"<path id='p-ollama-up' data-stack='local' class='arch-flow' d='M 316 452 C 342 452, 340 212, 316 202' stroke='#34d399' marker-end='url(#arr-local)'/>"
        f"<path id='p-gate-reg' data-stack='core loop' class='arch-flow-fast' d='M 540 150 L 540 184' stroke='#e8652b' marker-end='url(#arr-orange)'/>"
        f"<path id='p-reg-svc' data-stack='core' class='arch-flow-fast' d='M 540 264 L 540 302' stroke='#e8652b' marker-end='url(#arr-orange)'/>"
        f"<path id='p-agent-mcp' data-stack='core gcp local' class='arch-flow-fast' d='M 540 434 L 540 392' stroke='#f2743d' marker-end='url(#arr-orange)'/>"
        # Compiler -> GCP BigQuery MCP -> BigQuery Tables
        f"<path id='p-svc-bq' data-stack='gcp' class='arch-flow-fast' d='M 706 346 C 742 346, 730 108, 764 108' stroke='#38bdf8' marker-end='url(#arr-gcp)'/>"
        f"<path id='p-bq-ro' data-stack='gcp' class='arch-flow-fast' d='M 925 144 L 925 182' stroke='#38bdf8' marker-end='url(#arr-gcp)'/>"
        # Compiler -> DuckDB
        f"<path id='p-svc-duck' data-stack='local' class='arch-flow-fast' d='M 706 362 C 736 362, 736 418, 764 418' stroke='#34d399' marker-end='url(#arr-local)'/>"
        # Drift & Feedback return loop along bottom
        f"<path id='p-feedback' data-stack='loop' class='arch-flow' d='M 640 392 C 640 566, 460 566, 390 566 L 230 566 C 80 566, 42 548, 42 505 C 42 72, 340 72, 372 92' stroke='#fbbf24' marker-end='url(#arr-warn)'/>"

        # Glowing animated data packets along key paths
        f"<circle r='3.5' fill='#f2743d' data-stack='core'><animateMotion dur='2.4s' repeatCount='indefinite'><mpath href='#p-agent-mcp'/></animateMotion></circle>"
        f"<circle r='3.5' fill='#e8652b' data-stack='core loop'><animateMotion dur='2.8s' repeatCount='indefinite'><mpath href='#p-draft-gate'/></animateMotion></circle>"
        f"<circle r='3.5' fill='#38bdf8' data-stack='gcp'><animateMotion dur='2.6s' repeatCount='indefinite'><mpath href='#p-gemini-draft'/></animateMotion></circle>"
        f"<circle r='4' fill='#38bdf8' data-stack='gcp'><animateMotion dur='3.0s' repeatCount='indefinite'><mpath href='#p-svc-bq'/></animateMotion></circle>"
        f"<circle r='3.5' fill='#38bdf8' data-stack='gcp'><animateMotion dur='1.9s' repeatCount='indefinite'><mpath href='#p-bq-ro'/></animateMotion></circle>"
        f"<circle r='4' fill='#34d399' data-stack='local'><animateMotion dur='2.5s' repeatCount='indefinite'><mpath href='#p-svc-duck'/></animateMotion></circle>"
        f"<circle r='3.5' fill='#fbbf24' data-stack='loop'><animateMotion dur='6.5s' repeatCount='indefinite'><mpath href='#p-feedback'/></animateMotion></circle>"

        # PLANE 1 NODES
        f"<g class='arch-node' data-stack='core loop' data-title='Ingest, BQ Jobs &amp; Dataplex Policy Tags (src/semlayer/ingest, lineage)' "
        f"data-detail='Snapshots warehouse schema, ingests query_log.jsonl OR BigQuery INFORMATION_SCHEMA.JOBS_BY_PROJECT (--from-bq-jobs, domain-stripped owners), reads Dataplex COLUMN_FIELD_PATHS policy tags, and extracts sqlglot lineage.' data-adr='ADR-001 · ADR-012 · ADR-036'>"
        f"<rect x='34' y='54' width='282' height='58' rx='8' fill='#181514' stroke='#332d2a'/>"
        f"<text x='48' y='77' fill='#f5f4f2' font-size='12.5' font-weight='700'>Query Log / BQ JOBS + Policy Tags</text>"
        f"<text x='48' y='96' fill='#a8a29e' font-size='10.5' font-family='monospace'>schema · JOBS_BY_PROJECT · sqlglot</text></g>"

        f"<g class='arch-node' data-stack='core loop' data-title='Draft &amp; Structural Conflict Engine (src/semlayer/draft, conflicts, policy)' "
        f"data-detail='Clusters single-table aggregates, drafts FK entity edges (with cardinality), merges Dataplex policy tags + regex PII into deny_dims rules, and flags variants/duplicates for Split or Pick.' data-adr='ADR-012 · ADR-013 · ADR-036'>"
        f"<rect x='34' y='150' width='282' height='82' rx='8' fill='#181514' stroke='#e8652b' stroke-opacity='0.55'/>"
        f"<text x='48' y='173' fill='#f5f4f2' font-size='13' font-weight='700'>Draft Engine &amp; Conflict Detector</text>"
        f"<text x='48' y='192' fill='#a8a29e' font-size='10.5' font-family='monospace'>metrics · FK cardinality · Dataplex PII</text>"
        f"<text x='48' y='211' fill='#fbbf24' font-size='11' font-family='monospace'>variants / duplicates → Split · Pick</text></g>"

        f"<g class='arch-node' data-stack='gcp' data-title='Primary LLM · Gemini API via AI Studio + Secret Manager (src/semlayer/core/llm.py)' "
        f"data-detail='Default llm_provider: gemini (gemini-3.5-flash-lite) using GEMINI_API_KEY (.env locally or GCP Secret Manager on Cloud Run). 15 RPM pacer + HTTP 429 Retry-After backoff; words descriptions only.' data-adr='ADR-024 · ADR-029 · ADR-037'>"
        f"<rect x='34' y='302' width='282' height='78' rx='8' fill='#0f1c26' stroke='#38bdf8' stroke-opacity='0.75'/>"
        f"<text x='48' y='325' fill='#38bdf8' font-size='12.5' font-weight='700'>Gemini API · AI Studio (Primary LLM)</text>"
        f"<text x='48' y='344' fill='#e2e8f0' font-size='10.5' font-family='monospace'>gemini-3.5-flash-lite · Secret Manager</text>"
        f"<text x='48' y='362' fill='#94a3b8' font-size='10.5' font-family='monospace'>15 RPM · 429 Retry-After · wording</text></g>"

        f"<g class='arch-node' data-stack='local' data-title='Secondary LLM · Local Ollama / OpenAI-Compat (src/semlayer/core/llm.py)' "
        f"data-detail='Config-only switch to llm_provider: ollama (qwen3.5:9b) or openai (llama.cpp, LM Studio, vLLM), or --no-llm deterministic heuristics for air-gapped &amp; CI runs.' data-adr='ADR-006 · ADR-022'>"
        f"<rect x='34' y='416' width='282' height='76' rx='8' fill='#0f1d17' stroke='#34d399' stroke-opacity='0.7'/>"
        f"<text x='48' y='439' fill='#34d399' font-size='12.5' font-weight='700'>Ollama / Local OpenAI (Secondary)</text>"
        f"<text x='48' y='458' fill='#d1fae5' font-size='11' font-family='monospace'>qwen3.5:9b · OpenAI-compat · --no-llm</text>"
        f"<text x='48' y='476' fill='#86efac' font-size='10.5' font-family='monospace'>100% local private data &amp; keyless CI</text></g>"

        # PLANE 2 NODES
        f"<g class='arch-node' data-stack='core loop' data-title='Human Certification Gate (Web Console / Cloud Run IAM / CLI)' "
        f"data-detail='Invariants 1 &amp; 6: LLMs propose, humans certify. Transitions draft → certified (v+1) ↔ suspended → deprecated. Enforces certified inputs on ratio/derived metrics; drafts, _pending &amp; suspended NEVER served.' data-adr='ADR-003 · ADR-030 · ADR-031 · ADR-033'>"
        f"<rect class='arch-pulse-ring' x='370' y='52' width='340' height='100' rx='10' fill='none' stroke='#e8652b' stroke-width='2'/>"
        f"<rect x='374' y='56' width='332' height='92' rx='8' fill='#241510' stroke='#e8652b' stroke-width='1.5'/>"
        f"<text x='390' y='80' fill='#f2743d' font-size='13.5' font-weight='700'>★ Human Certification Gate (UI / CLI)</text>"
        f"<text x='390' y='100' fill='#f5f4f2' font-size='10.5' font-family='monospace'>draft → certified ↔ suspended → deprecated</text>"
        f"<text x='390' y='118' fill='#d6d3d1' font-size='10.5' font-family='monospace'>owner + certified dep DAG · Cloud Run IAM</text>"
        f"<text x='390' y='135' fill='#fbbf24' font-size='10.5' font-family='monospace'>drafts, _pending &amp; suspended NEVER served</text></g>"

        f"<g class='arch-node' data-stack='core loop' data-title='YAML Registry + SQLite FTS5 Search Index (src/semlayer/registry)' "
        f"data-detail='Git-diffable YAML source of truth + rebuildable SQLite &amp; FTS5 BM25 index (search_metrics over names, concepts, dims &amp; synonyms). Tracks ratio/derived DAG cycles and emits Cloud Logging JSON.' data-adr='ADR-003 · ADR-033 · ADR-035 · ADR-037'>"
        f"<rect x='374' y='186' width='332' height='78' rx='8' fill='#181514' stroke='#38312e'/>"
        f"<text x='390' y='209' fill='#f5f4f2' font-size='13' font-weight='700'>Certified Registry (YAML + FTS5)</text>"
        f"<text x='390' y='228' fill='#a8a29e' font-size='10.5' font-family='monospace'>simple · ratio · derived · FTS5 BM25</text>"
        f"<text x='390' y='246' fill='#736c67' font-size='10.5' font-family='monospace'>audit.jsonl + Cloud Logging stderr JSON</text></g>"

        f"<g class='arch-node' data-stack='core gcp local' data-title='Governed Service, Bound Role &amp; AST Compiler (src/semlayer/serve, compiler, graph)' "
        f"data-detail='Enforces strict bound --role, resource caps, per-dependency RBAC, M:1 edge joins (blocking 1:N fan-out), time grains/ranges, typed filter ops, and NULLIF multi-metric CTE compilation via sqlglot ASTs.' data-adr='ADR-031 · ADR-032 · ADR-033 · ADR-034'>"
        f"<rect x='374' y='304' width='332' height='88' rx='8' fill='#1b1715' stroke='#e8652b'/>"
        f"<text x='390' y='327' fill='#f5f4f2' font-size='13' font-weight='700'>Service · Bound RBAC · AST Compiler</text>"
        f"<text x='390' y='346' fill='#f2743d' font-size='10.5' font-family='monospace'>--role strict · caps · M:1 join guard</text>"
        f"<text x='390' y='364' fill='#a8a29e' font-size='10.5' font-family='monospace'>QuerySpec · time grains · CTE ratios</text>"
        f"<text x='390' y='381' fill='#34d399' font-size='10.5' font-family='monospace'>Runtime: {e(active_mode)}</text></g>"

        f"<g class='arch-node' data-stack='core gcp local' data-title='AI Agent / MCP Client Surface (src/semlayer/serve/mcp_server.py)' "
        f"data-detail='Agents connect over stdio MCP (or Google ADK McpToolset) to 9 governed tools: list_metrics, search_metrics, describe_metric, query_metric, query_metrics, describe_graph, traverse_graph, report_gap, report_issue.' data-adr='ADR-004 · ADR-032 · ADR-033 · ADR-035'>"
        f"<rect x='374' y='434' width='332' height='74' rx='8' fill='#141211' stroke='#443c38'/>"
        f"<text x='390' y='457' fill='#f5f4f2' font-size='13' font-weight='700'>AI Agents &amp; ADK (9 Governed MCP Tools)</text>"
        f"<text x='390' y='476' fill='#a8a29e' font-size='10.5' font-family='monospace'>search_metrics · query_metric(s) · graph</text>"
        f"<text x='390' y='493' fill='#f87171' font-size='10.5' font-family='monospace'>NO raw-SQL tool · NO warehouse credentials</text></g>"

        # PLANE 3A: GCP BIGQUERY MCP NODES
        f"<g class='arch-node' data-stack='gcp' data-title='Google Managed BigQuery MCP Server (src/semlayer/warehouse/bigquery.py)' "
        f"data-detail='Streamable HTTP MCP client to https://bigquery.googleapis.com/mcp with ADC / SA impersonation. Invokes execute_sql_readonly ONLY with dryRun byte cap, ISOWEEK DATE_TRUNC, and sqlglot AST literals.' data-adr='ADR-026 · ADR-027 · ADR-028 · ADR-032'>"
        f"<rect x='764' y='56' width='322' height='88' rx='8' fill='#102030' stroke='#38bdf8'/>"
        f"<text x='778' y='79' fill='#38bdf8' font-size='13' font-weight='700'>BigQuery Managed MCP (HTTPS)</text>"
        f"<text x='778' y='98' fill='#e2e8f0' font-size='11' font-family='monospace'>bigquery.googleapis.com/mcp · ADC OAuth</text>"
        f"<text x='778' y='116' fill='#7dd3fc' font-size='10.5' font-family='monospace'>execute_sql_readonly + dryRun byte cap</text>"
        f"<text x='778' y='133' fill='#94a3b8' font-size='10.5' font-family='monospace'>sqlglot AST · ISOWEEK · CTE FULL JOIN</text></g>"

        f"<g class='arch-node' data-stack='gcp' data-title='BigQuery + Dataplex + Cloud Run Ops ({e(bq_proj)}.{e(bq_ds)})' "
        f"data-detail='Read-only tables, INFORMATION_SCHEMA.JOBS_BY_PROJECT query history, Dataplex policy tags, Cloud Run IAM console + scheduled Canary Job, and Materialized View DDL advisor.' data-adr='ADR-025 · ADR-036 · ADR-037 · ADR-038'>"
        f"<rect x='764' y='184' width='322' height='92' rx='8' fill='#0d1824' stroke='#1e496b'/>"
        f"<text x='778' y='207' fill='#f5f4f2' font-size='12.5' font-weight='700'>BigQuery + Dataplex + Cloud Run Ops</text>"
        f"<text x='778' y='226' fill='#38bdf8' font-size='11' font-family='monospace'>{e(bq_target)}</text>"
        f"<text x='778' y='245' fill='#94a3b8' font-size='10.5' font-family='monospace'>JOBS_BY_PROJECT · Policy Tags · MV DDL</text>"
        f"<text x='778' y='263' fill='#34d399' font-size='10.5' font-family='monospace'>Cloud Run IAM · Canary Job · 33/33 eval</text></g>"

        # PLANE 3B: LOCAL DUCKDB NODE
        f"<g class='arch-node' data-stack='local' data-title='DuckDB Local Read-Only Warehouse (src/semlayer/warehouse/__init__.py)' "
        f"data-detail='Default zero-config embedded warehouse (read_only=True). Executes AST-compiled SQL, multi-metric CTEs, ISO week truncation, and M:1 joins with bound ? parameters and zero network calls.' data-adr='ADR-009 · ADR-032'>"
        f"<rect x='764' y='356' width='322' height='148' rx='8' fill='#0e1f18' stroke='#34d399'/>"
        f"<text x='778' y='382' fill='#34d399' font-size='13' font-weight='700'>DuckDBWarehouse (Local Default)</text>"
        f"<text x='778' y='403' fill='#e2e8f0' font-size='11' font-family='monospace'>examples/jaffle_shop/jaffle.duckdb</text>"
        f"<text x='778' y='424' fill='#a7f3d0' font-size='11' font-family='monospace'>read_only=True · bound ? parameters</text>"
        f"<text x='778' y='444' fill='#86efac' font-size='10.5' font-family='monospace'>DATE_TRUNC · CTE ratios · M:1 joins</text>"
        f"<text x='778' y='464' fill='#94a3b8' font-size='10.5' font-family='monospace'>Lazy file open · instant offline CI</text>"
        f"<text x='778' y='484' fill='#34d399' font-size='10.5' font-family='monospace'>Verified: 104/104 offline pytest suite</text></g>"

        # PLANE 4: DRIFT, CANARY & FEEDBACK BAR
        f"<g class='arch-node' data-stack='loop' data-title='Canary Probes, Drift Monitor &amp; MV Advisor (src/semlayer/monitor)' "
        f"data-detail='semlayer canary runs LIMIT 0 probes and suspends broken metrics (cascading to derived dependents); drift opens _pending revisions; feedback clusters gaps and emits BigQuery MV DDL.' data-adr='ADR-019 · ADR-031 · ADR-038'>"
        f"<rect x='194' y='542' width='512' height='52' rx='8' fill='#1f190e' stroke='#fbbf24' stroke-opacity='0.75'/>"
        f"<text x='210' y='564' fill='#fbbf24' font-size='12.5' font-weight='700'>PLANE 4 · Canary Health, Drift &amp; BigQuery MV Advisor</text>"
        f"<text x='210' y='582' fill='#fde68a' font-size='10.5' font-family='monospace'>canary LIMIT 0 → suspended · drift → _pending · MV DDL</text></g>"

        f"</svg>"
        f"<div class='arch-inspector'>"
        f"<span id='arch-ins-title' class='arch-ins-title'>Hover or click any node to inspect module &amp; invariants</span>"
        f"<span id='arch-ins-detail' class='arch-ins-detail'>Use the stack pills above to isolate the GCP Cloud path (Gemini + BigQuery MCP + Cloud Run), the Local Offline path (Ollama + DuckDB), or the Canary &amp; Drift loop.</span>"
        f"<span id='arch-ins-adr' class='badge badge-orange'>ADR-001 → ADR-038</span>"
        f"</div></div></section>"

        f"<section id='sys-planes'><h2>Four architectural planes</h2>"
        f"<div class='card-grid'>"
        f"<div class='card'><div class='card-head'><span class='card-title'>1. Ingest, Draft &amp; Conflict Plane</span><span class='badge badge-orange'>Propose</span></div>"
        f"<div style='color:var(--text-secondary);font-size:13px'>Ingests local JSONL logs or BigQuery <code>INFORMATION_SCHEMA.JOBS_BY_PROJECT</code> plus Dataplex policy tags. Drafts metrics, FK cardinality edges, and PII <code>deny_dims</code> rules while Gemini or Ollama writes filter-aware descriptions.</div></div>"
        f"<div class='card'><div class='card-head'><span class='card-title'>2. Certification, Bound RBAC &amp; AST Compiler</span><span class='badge badge-ok'>Enforce</span></div>"
        f"<div style='color:var(--text-secondary);font-size:13px'>YAML + SQLite FTS5 registry (<code>draft → certified ↔ suspended → deprecated</code>). Enforces strict <code>serve --role</code> binding, per-dependency RBAC, safe M:1 joins (blocking 1:N fan-out), time grains/ranges, and multi-metric CTEs.</div></div>"
        f"<div class='card'><div class='card-head'><span class='card-title'>3. Dual Warehouse &amp; Cloud Run Execution</span><span class='badge' style='color:#38bdf8;border-color:#1e496b'>GCP + Local</span></div>"
        f"<div style='color:var(--text-secondary);font-size:13px'>Runs identically on embedded <code>DuckDBWarehouse</code> (bound parameters) or Google's managed <code>BigQueryMCP</code> (<code>execute_sql_readonly</code> + <code>dryRun</code> byte cap + <code>sqlglot</code> AST rendering), deployable on Cloud Run with Secret Manager.</div></div>"
        f"<div class='card'><div class='card-head'><span class='card-title'>4. Canary Health, Drift &amp; MV Advisor</span><span class='badge badge-warn'>Observe</span></div>"
        f"<div style='color:var(--text-secondary);font-size:13px'>Scheduled <code>semlayer canary</code> probes certified metrics with <code>LIMIT 0</code> and suspends broken definitions automatically; Cloud Logging stderr JSON powers alerts; <code>feedback --recommend-mv</code> emits BigQuery MV DDL.</div></div>"
        f"</div></section>"

        f"<section id='sys-matrix'><h2>GCP Cloud vs. Local stack comparison</h2>"
        f"<div class='table-wrap'><table><tr><th>Subsystem</th><th>GCP Cloud Mode (Primary LLM / Cloud Warehouse / Cloud Run)</th><th>Local / Offline Mode (Secondary LLM / Default Warehouse)</th></tr>"
        f"<tr><td><b class='def-name'>LLM Provider</b></td><td><code>llm_provider: gemini</code> · <code>gemini-3.5-flash-lite</code> via AI Studio (<code>GEMINI_API_KEY</code> in Secret Manager or <code>.env</code>, 15 RPM pacing, <code>429 Retry-After</code>)</td><td><code>llm_provider: ollama | openai | none</code> · <code>qwen3.5:9b</code> on <code>localhost:11434</code> or deterministic heuristics (<code>--no-llm</code>)</td></tr>"
        f"<tr><td><b class='def-name'>Query &amp; PII Ingestion</b></td><td><code>semlayer ingest --from-bq-jobs</code> (<code>JOBS_BY_PROJECT</code> with domain-stripped owners) + Dataplex policy tags (<code>COLUMN_FIELD_PATHS</code> → auto <code>deny_dims</code>)</td><td>Local <code>query_log.jsonl</code> / <code>.sql</code> + <code>dbt manifest.json</code> + column-name regex sensitivity heuristics</td></tr>"
        f"<tr><td><b class='def-name'>Warehouse Adapter</b></td><td><code>BigQueryMCP</code> → <code>https://bigquery.googleapis.com/mcp</code> over Streamable HTTP (<code>execute_sql_readonly</code> only)</td><td><code>DuckDBWarehouse</code> → embedded <code>jaffle.duckdb</code> opened with <code>read_only=True</code></td></tr>"
        f"<tr><td><b class='def-name'>Compiler &amp; Analytics</b></td><td><code>sqlglot</code> AST <code>QuerySpec</code>: <code>ISOWEEK</code> / <code>DATE_TRUNC</code>, typed operators, <code>NULLIF</code> ratio/derived CTEs (<code>FULL OUTER JOIN IS NOT DISTINCT FROM</code>), M:1 joins</td><td>Identical <code>QuerySpec</code> AST semantics compiled to DuckDB with bound <code>?</code> parameters and <code>list_contains(?, key)</code></td></tr>"
        f"<tr><td><b class='def-name'>Ops, Auth &amp; Telemetry</b></td><td>Cloud Run IAM (<code>--no-allow-unauthenticated</code>), strict <code>--role</code> binding, Cloud Scheduler <code>canary</code> Job, Cloud Logging JSON on <code>stderr</code>, BigQuery MV DDL advisor</td><td><code>127.0.0.1</code> console, strict <code>--role</code> CLI flag, local <code>audit.jsonl</code> / <code>usage.jsonl</code>, 104/104 keyless offline CI tests</td></tr>"
        f"</table></div></section>"

        f"<section id='sys-invariants'><h2>Enforced runtime invariants</h2>"
        f"<div class='table-wrap'><table><tr><th>#</th><th>Invariant</th><th>Enforcement Module</th></tr>"
        f"<tr><td class='mono'>1</td><td>Drafts, <code>_pending</code> revisions, <code>suspended</code>, and <code>deprecated</code> definitions are never served to agents.</td><td><code>serve/service.py</code> · <code>registry/registry.py</code></td></tr>"
        f"<tr><td class='mono'>2</td><td>Every metric answer carries certified <code>metric</code> name, <code>version</code>, and <code>owner</code>.</td><td><code>serve/service.py</code></td></tr>"
        f"<tr><td class='mono'>3</td><td>Strict <code>serve --role</code> rejects caller-supplied <code>as_role</code>; default-deny RBAC, <code>row_filter</code>, and <code>deny_dims</code> apply to every metric and transitive ratio/derived input.</td><td><code>serve/service.py</code> · <code>policy/__init__.py</code></td></tr>"
        f"<tr><td class='mono'>4</td><td>Cross-entity dimensions join only along unique shortest <code>many_to_one</code> / <code>one_to_one</code> paths; <code>one_to_many</code> fan-out joins are refused.</td><td><code>graph/__init__.py</code> · <code>serve/service.py</code></td></tr>"
        f"<tr><td class='mono'>5</td><td>Multi-metric &amp; ratio queries aggregate each leaf metric in its own CTE before joining on grain dimensions (zero cross-multiplication) with <code>NULLIF(..., 0)</code> division.</td><td><code>compiler/__init__.py</code></td></tr>"
        f"<tr><td class='mono'>6</td><td>No raw-SQL MCP tool exists; <code>BigQueryMCP</code> calls only <code>execute_sql_readonly</code> with pre-flight <code>dryRun</code> byte caps and AST-bound literals.</td><td><code>serve/mcp_server.py</code> · <code>warehouse/bigquery.py</code></td></tr>"
        f"<tr><td class='mono'>7</td><td><code>semlayer canary</code> probes certified metrics with <code>LIMIT 0</code> and cascades failures into <code>suspended</code>; Cloud Logging JSON writes strictly to <code>stderr</code>.</td><td><code>monitor/__init__.py</code> · <code>core/config.py</code></td></tr>"
        f"<tr><td class='mono'>8</td><td>Sensitive &amp; Dataplex policy-tagged column values are stripped from LLM prompts; quota/transport errors abort eval runs immediately.</td><td><code>evaluate.py</code> · <code>policy/__init__.py</code></td></tr>"
        f"</table></div></section>"
        f"</main></div>"
    )


def create_app(reg: Registry, svc: Service) -> FastAPI:
    app = FastAPI(title="semlayer")

    @app.get("/api/metrics")
    def metrics():
        return svc.list_metrics()

    @app.get("/api/queue")
    def queue():
        return [{"kind": n.kind, "name": n.name, "usage": n.evidence.usage,
                 "revision": bool(n.version)} for k in ("metric", "entity", "edge", "rule")
                for n in reg.queue(k)]

    @app.post("/api/certify/{kind}/{name}")
    def api_certify(kind: str, name: str, owner: str = ""):
        if kind not in KINDS:
            return JSONResponse({"error": f"unknown kind: {kind}"}, status_code=400)
        try:
            n = reg.certify(kind, name, owner=owner or None, by="api")
        except (InvalidTransition, KeyError, ValueError) as ex:
            return JSONResponse({"error": str(ex)}, status_code=400)
        return {"name": n.name, "version": n.version, "state": n.state.value}

    @app.get("/", response_class=HTMLResponse)
    def index(tab: str = "governance"):
        active_tab = "system" if tab == "system" else "governance"
        q_items = [(k, n) for k in ("metric", "entity", "edge", "rule") for n in reg.queue(k)]
        rows = "".join(
            _row(n, diff_text=reg.diff(k, n.name) if n.version else "")
            for k, n in q_items
        )
        empty = (
            '<tr><td colspan=5><div class="empty-card"><div><strong>Review queue is clear</strong> · '
            'No pending drafts or drift revisions awaiting sign-off.</div>'
            '<span class="badge badge-ok">All certified</span></div></td></tr>'
        )

        detected = detect(reg.list("metric"))
        conflicts_html = (
            f'<div class="card-grid">{"".join(_conflict_card(c) for c in detected)}</div>'
            if detected else (
                '<div class="empty-card"><div><strong>Zero metric conflicts</strong> · '
                'No duplicate logic or competing variant definitions detected.</div>'
                '<span class="badge badge-ok">Clean</span></div>'
            )
        )

        cert_metrics = reg.certified("metric")
        susp_metrics = reg.list("metric", State.suspended)
        cert_entities = reg.certified("entity")
        cert_edges = reg.certified("edge")
        cert_rules = reg.certified("rule")
        wh_dialect = getattr(svc.wh, "dialect", "duckdb")
        bq_proj = getattr(svc.wh, "project", "") or "your-gcp-project"
        bq_ds = getattr(svc.wh, "dataset", "jaffle_shop")

        cert_by_name = {m.name: m for m in cert_metrics}
        cert_rows = []
        for m in cert_metrics:
            duck_sql, bq_sql = _compiled_preview(m, cert_by_name, bq_proj, bq_ds)
            type_badge = (
                f' <span class="badge badge-orange">{e(m.metric_type)}</span>'
                if m.metric_type != "simple"
                else ""
            )
            extra_meta = []
            if m.time_dimension:
                extra_meta.append(f"time: {m.time_dimension}")
            if m.synonyms:
                extra_meta.append(f"synonyms: {', '.join(m.synonyms)}")
            extra_html = (
                f'<div class="meta-row"><span><code>{e(" · ".join(extra_meta))}</code></span></div>'
                if extra_meta
                else ""
            )
            cert_rows.append(
                f'<tr data-filterable>'
                f'<td><b class="def-name">{e(m.name)}</b> <span class="badge badge-ok">v{m.version}</span>{type_badge}'
                f'<div class="meta-row"><span>owner: <code>{e(m.owner or "-")}</code></span></div></td>'
                f'<td><code class="sql-chip">{e(_summary(m))}</code>'
                f'<div class="desc-text">{e(m.description or "(no description)")}</div></td>'
                f'<td><code>{e(", ".join(m.dimensions) or "-")}</code>'
                f'<div class="meta-row"><span>entity: <code>{e(m.entity or "-")}</code></span></div>{extra_html}</td>'
                f'<td><details><summary>compiled SQL (DuckDB &amp; BigQuery)</summary>'
                f'<div class="meta-row"><span>DuckDB (parameterised):</span></div><pre>{e(duck_sql)}</pre>'
                f'<div class="meta-row"><span>BigQuery MCP (sqlglot AST):</span></div><pre>{e(bq_sql)}</pre>'
                f'<div class="meta-row"><span>lineage: <code>{e(", ".join(m.evidence.lineage) or "-")}</code></span></div>'
                f"</details></td></tr>"
            )
        cert_section = (
            f'<div class="table-wrap"><table><tr><th>metric</th><th>expression &amp; description</th>'
            f'<th>dimensions / time / entity</th><th>dialect SQL &amp; lineage</th></tr>{"".join(cert_rows)}</table></div>'
            if cert_rows else '<p class="empty-note">None certified yet</p>'
        )

        susp_rows = "".join(
            f'<tr id="metric-{e(sm.name)}" data-filterable>'
            f'<td><b class="def-name">{e(sm.name)}</b> <span class="badge badge-err">suspended</span></td>'
            f'<td><code class="sql-chip">{e(_summary(sm))}</code></td>'
            f'<td><code style="color:var(--err)">{e(sm.suspend_reason or "canary failed")}</code></td>'
            f'<td><form class="act-inline" method="post" action="/certify/metric/{e(sm.name)}">'
            f'<input type="hidden" name="owner" value="{e(sm.owner or "data-team")}">'
            f'<button class="btn-primary" type="submit">re-certify</button></form></td></tr>'
            for sm in susp_metrics
        )
        susp_section = (
            f"<section id='sec-suspended' data-ws-group='review'><h2>Suspended metrics <span class='badge badge-err'>{len(susp_metrics)}</span></h2>"
            f"<p class='sec-sub'>Automatically quarantined by <code>semlayer canary</code> (never served to agents until fixed and re-certified).</p>"
            f"<div class='table-wrap'><table><tr><th>metric</th><th>logic</th><th>suspend reason</th><th>action</th></tr>"
            f"{susp_rows}</table></div></section>"
            if susp_metrics
            else ""
        )

        ent_rows = "".join(
            f'<tr data-filterable><td><span class="badge">entity</span> <b class="def-name">{e(en.name)}</b> '
            f'<span class="badge badge-ok">v{en.version}</span></td>'
            f'<td><code>{e(en.table)}</code></td><td><code>{e(en.key)}</code></td>'
            f'<td><code>{e(en.owner or "-")}</code></td></tr>'
            for en in cert_entities
        )
        edge_rows = "".join(
            f'<tr data-filterable><td><span class="badge badge-orange">{e(ed.rel)}</span></td>'
            f'<td><code>{e(ed.src)}.{e(ed.src_key)}</code> → <code>{e(ed.dst)}.{e(ed.dst_key)}</code></td>'
            f'<td><span class="badge">{e(ed.cardinality)}</span></td>'
            f'<td><span class="badge badge-ok">v{ed.version}</span></td></tr>'
            for ed in cert_edges
        )

        rule_rows = "".join(
            f'<tr data-filterable><td><b class="def-name">{e(r.name)}</b> <span class="badge badge-ok">v{r.version}</span></td>'
            f'<td><code>{e(r.role)}</code></td><td><code>{e(r.metric)}</code></td>'
            f'<td><span class="badge {"badge-ok" if r.effect == "allow" else "badge-err"}">{e(r.effect.upper())}</span></td>'
            f'<td><code>{e(r.row_filter or "-")}</code></td>'
            f'<td><code>{e(", ".join(r.deny_dims) or "-")}</code></td></tr>'
            for r in cert_rules
        ) or '<tr><td colspan=6><i>No certified rules — default deny blocks all queries.</i></td></tr>'

        usage_events = read_usage(reg)
        fb = feedback_summary(usage_events, min_volume=1)
        demand_str = ", ".join(f"{m} ({n})" for m, n in fb["demand"][:6]) or "none yet"
        gaps_str = ", ".join(f'"{g}" ({n})' for g, n in fb["missing"][:5]) or "none reported"
        flagged_str = ", ".join(fb["flagged"]) or "none"
        mv_recs = [
            r for r in recommend_materialized_views(reg, usage_events, project=bq_proj, dataset=bq_ds, min_volume=1)
            if r.get("eligible") and r.get("ddl")
        ]
        mv_html = (
            f"<details style='margin-top:12px'><summary>BigQuery Materialized View Advisor ({len(mv_recs)} eligible hot metric(s))</summary>"
            + "".join(f"<pre>{e(r['ddl'])}</pre>" for r in mv_recs[:3])
            + "</details>"
            if mv_recs
            else ""
        )

        audits = list(reversed(reg.audit()[-12:]))
        audit_rows = "".join(
            f'<tr data-filterable>'
            f'<td class="mono">{datetime.fromtimestamp(a["ts"], UTC).strftime("%Y-%m-%d %H:%M:%S")} UTC</td>'
            f'<td><span class="badge">{e(a["action"])}</span></td>'
            f'<td><code>{e(a["kind"])}/{e(a["name"])}</code></td>'
            f'<td><code>{e(a.get("by", "-"))}</code></td>'
            f'<td class="mono">{e(str(a.get("version", "-")))} (edit_ratio={e(str(a.get("edit_ratio", "-")))})</td></tr>'
            for a in audits
        ) or '<tr><td colspan=5><i>No audit events recorded yet.</i></td></tr>'

        metric_opts = "".join(f'<option value="{e(m.name)}">{e(m.name)}</option>' for m in cert_metrics)
        entity_opts = "".join(f'<option value="{e(en.name)}">{e(en.name)}</option>' for en in cert_entities)
        known_roles = sorted({r.role for r in cert_rules} | {"finance", "eu_analyst", "guest"})
        role_opts = "".join(f'<option value="{e(rl)}">{e(rl)}</option>' for rl in known_roles)

        wh_badge = "BigQuery MCP (HTTPS)" if wh_dialect == "bigquery" else "DuckDB (Local Read-Only)"
        gov_cls = "shell tab-view" if active_tab == "governance" else "shell tab-view hidden"
        gov_tab_cls = "top-tab active" if active_tab == "governance" else "top-tab"
        sys_tab_cls = "top-tab active" if active_tab == "system" else "top-tab"
        sys_html = _system_view(
            wh_dialect, bq_proj, bq_ds,
            len(cert_metrics), len(cert_rules), len(cert_edges),
            hidden=(active_tab != "system"),
        )

        bound_badge = (
            f"<span class='badge badge-ok'>role: {e(svc.bound_role)} (strict)</span>"
            if svc.bound_role
            else ""
        )
        role_field_q = (
            f"<div class='form-row'><label>Bound role</label><input value='{e(svc.bound_role)}' disabled></div>"
            if svc.bound_role
            else f"<div class='form-row'><label>As role</label><select name='as_role'>{role_opts}</select></div>"
        )
        role_field_t = (
            f"<div class='form-row'><label>Bound role</label><input value='{e(svc.bound_role)}' disabled></div>"
            if svc.bound_role
            else f"<div class='form-row'><label>As role</label><select name='as_role'>{role_opts}</select></div>"
        )

        status_dot_cls = "status-dot err" if susp_metrics else ("status-dot warn" if (q_items or detected) else "status-dot")
        status_text = (
            f"{len(susp_metrics)} suspended metric(s) quarantined"
            if susp_metrics
            else (
                f"{len(q_items)} pending review · {len(detected)} conflict(s)"
                if (q_items or detected)
                else f"All {len(cert_metrics)} certified metrics healthy"
            )
        )
        presets = [
            ("Revenue by method", {"metric": "revenue", "as_role": "finance", "dims": "method"}, False),
            ("EU row_filter", {"metric": "customer_count", "as_role": "eu_analyst"}, False),
            ("RBAC deny test", {"metric": "revenue", "as_role": "eu_analyst"}, True),
            ("M:1 join (Customer.region)", {"metric": "revenue", "as_role": "finance", "dims": "Customer.region"}, False),
            ("1:N fan-out trap", {"metric": "customer_count", "as_role": "finance", "dims": "Order.status"}, True),
            ("Traverse returned orders", {"kind": "traverse", "start": "Customer", "path": "HAS_ORDER", "as_role": "finance", "filters": "Order.status=returned"}, False),
        ]
        preset_btns = "".join(
            f"<button type='button' class='preset-btn{' danger-chip' if is_d else ''}' "
            f"data-preset='{e(json.dumps(cfg))}'>{e(label)}</button>"
            for label, cfg, is_d in presets
        )

        return (
            f"<!doctype html><html lang='en'><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>ARTH: Semantic Layer · Governance &amp; System</title>{STYLE}{HTMX}{SCRIPT}</head><body>"
            f"<header class='topbar'>"
            f"<div class='brand-wrap'>"
            f"<a href='/?tab=governance' class='brand'><span class='brand-dot'></span>"
            f"<span>ARTH:</span>"
            f"<span id='brand-roller' class='brand-roller'><span id='brand-reel' class='brand-reel'>"
            f"<span class='reel-word'>Semantic</span>"
            f"<span class='reel-word reel-dhi'>Dhī</span>"
            f"<span class='reel-word' aria-hidden='true'>Semantic</span>"
            f"</span></span>"
            f"<span>Layer</span></a>"
            f"<nav class='top-tabs' aria-label='Console views'>"
            f"<a href='/?tab=governance' class='{gov_tab_cls}' data-tab-btn='governance'>Governance Console</a>"
            f"<a href='/?tab=system' class='{sys_tab_cls}' data-tab-btn='system'>System</a>"
            f"</nav>"
            f"</div>"
            f"<div class='top-meta'>"
            f"<div class='search-pill'><span style='color:var(--text-muted)'>⌕</span>"
            f"<input id='global-search' type='search' placeholder='Filter metrics, SQL, lineage, rules...'>"
            f"<span class='kbd'>Ctrl K</span></div>"
            f"{bound_badge}<span class='badge badge-orange'>{e(wh_badge)}</span>"
            f"</div></header>"
            f"<div id='view-governance' class='{gov_cls}'>"
            f"<aside class='sidebar'>"
            f"<div class='nav-group'><div class='nav-title'>Governance</div>"
            f"<a class='nav-link active' href='#sec-queue'><span>Review queue</span><span class='nav-count'>{len(q_items)}</span></a>"
            f"<a class='nav-link' href='#sec-conflicts'><span>Conflicts (Split / Pick)</span><span class='nav-count'>{len(detected)}</span></a>"
            f"<a class='nav-link' href='#sec-feedback'><span>Canary, drift &amp; MV advisor</span></a></div>"
            f"<div class='nav-group'><div class='nav-title'>Certified Catalog</div>"
            f"<a class='nav-link' href='#sec-metrics'><span>Certified metrics</span><span class='nav-count'>{len(cert_metrics)}</span></a>"
            f"<a class='nav-link' href='#sec-graph'><span>Entity graph &amp; edges</span><span class='nav-count'>{len(cert_entities) + len(cert_edges)}</span></a>"
            f"<a class='nav-link' href='#sec-policy'><span>RBAC &amp; row-filter rules</span><span class='nav-count'>{len(cert_rules)}</span></a></div>"
            f"<div class='nav-group'><div class='nav-title'>Verification &amp; Ops</div>"
            f"<a class='nav-link' href='#sec-playground'><span>Governed query runner</span></a>"
            f"<a class='nav-link' href='#sec-audit'><span>Audit log (JSONL)</span><span class='nav-count'>{len(audits)}</span></a>"
            f"<a class='nav-link' href='#sec-mcp'><span>MCP &amp; REST surface</span></a></div>"
            f"</aside>"
            f"<main class='content'>"
            f"<div class='hero-card'><div class='hero-top'><div>"
            f"<div class='status-pill'><span class='{status_dot_cls}'></span><span>{e(status_text)}</span></div>"
            f"<h1>ARTH: SemLayer · Governance Console</h1>"
            f"<p class='lead' style='margin-bottom:0'>Certify metrics, ratios, FK edges, and RBAC rules before agents touch your data. "
            f"Use the workspace tabs below to focus, or launch a 1-click governed query test.</p>"
            f"</div><div class='hero-actions'>"
            f"<a class='btn-ghost' href='#sec-playground'>▶ Query Playground</a>"
            f"<a class='btn-ghost' href='/?tab=system' data-tab-btn='system'>◈ System Diagram</a>"
            f"</div></div></div>"
            f"<div class='stats'>"
            f"<a class='stat' href='#sec-queue'><div class='stat-k'>Pending review</div><div class='stat-v accent'>{len(q_items)}</div><div class='stat-sub'>drafts &amp; drift revisions</div></a>"
            f"<a class='stat' href='#sec-conflicts'><div class='stat-k'>Open conflicts</div><div class='stat-v'>{len(detected)}</div><div class='stat-sub'>variants &amp; duplicates</div></a>"
            f"<a class='stat' href='#sec-metrics'><div class='stat-k'>Certified / Suspended</div><div class='stat-v {'err' if susp_metrics else 'ok'}'>{len(cert_metrics)} / {len(susp_metrics)}</div><div class='stat-sub'>served / canary-quarantined</div></a>"
            f"<a class='stat' href='#sec-graph'><div class='stat-k'>Graph topology</div><div class='stat-v'>{len(cert_entities)} / {len(cert_edges)}</div><div class='stat-sub'>entities / M:1 &amp; 1:N edges</div></a>"
            f"<a class='stat' href='#sec-policy'><div class='stat-k'>Active rules</div><div class='stat-v'>{len(cert_rules)}</div><div class='stat-sub'>default-deny + Dataplex PII</div></a>"
            f"<a class='stat' href='#sec-mcp'><div class='stat-k'>Warehouse dialect</div><div class='stat-v'>{e(wh_dialect)}</div><div class='stat-sub'>execute_sql_readonly</div></a>"
            f"</div>"
            f"<div class='ws-tabs' role='tablist' aria-label='Console sections'>"
            f"<button type='button' class='ws-tab active' data-ws-tab='all'>Overview (All)</button>"
            f"<button type='button' class='ws-tab' data-ws-tab='review'>Review &amp; Conflicts ({len(q_items) + len(detected)})</button>"
            f"<button type='button' class='ws-tab' data-ws-tab='catalog'>Catalog &amp; Policy ({len(cert_metrics)})</button>"
            f"<button type='button' class='ws-tab' data-ws-tab='playground'>Playground &amp; Canary</button>"
            f"<button type='button' class='ws-tab' data-ws-tab='ops'>Ops &amp; Audit</button>"
            f"</div>"
            f"<section id='sec-queue' data-ws-group='review'>"
            f"<div class='table-wrap'><table><tr><th>definition</th><th>logic &amp; description</th><th>usage</th>"
            f"<th>evidence &amp; diff</th><th>action</th></tr>{rows or empty}</table></div></section>"
            f"{susp_section}"
            f"<section id='sec-conflicts' data-ws-group='review'><h2>Conflicts</h2>"
            f"<p class='sec-sub'>Structural variant clusters (same concept name, different SQL filter/agg) and duplicate logic under distinct aliases.</p>"
            f"{conflicts_html}</section>"
            f"<section id='sec-metrics' data-ws-group='catalog'><h2>Certified metrics</h2>"
            f"<p class='sec-sub'>Live governed definitions (simple, ratio, derived) exposed via <code>list_metrics</code>, <code>search_metrics</code>, <code>describe_metric</code>, and <code>query_metric(s)</code>.</p>"
            f"{cert_section}</section>"
            f"<section id='sec-graph' data-ws-group='catalog'><h2>Entity graph &amp; foreign-key edges</h2>"
            f"<p class='sec-sub'>Certified join topology powering safe M:1 dimension joins (blocking 1:N fan-out) and multi-hop <code>traverse_graph</code> entity-set filters.</p>"
            f"<div class='card-grid'>"
            f"<div class='table-wrap'><table><tr><th>entity</th><th>table</th><th>PK</th><th>owner</th></tr>"
            f"{ent_rows or '<tr><td colspan=4><i>No certified entities</i></td></tr>'}</table></div>"
            f"<div class='table-wrap'><table><tr><th>relationship</th><th>join condition</th><th>cardinality</th><th>ver</th></tr>"
            f"{edge_rows or '<tr><td colspan=4><i>No certified edges</i></td></tr>'}</table></div>"
            f"</div></section>"
            f"<section id='sec-policy' data-ws-group='catalog'><h2>Access policy rules (default deny)</h2>"
            f"<p class='sec-sub'>Only certified rules apply; explicit <code>DENY</code> overrides <code>ALLOW</code>; restricted &amp; Dataplex policy-tagged dimensions cannot be grouped or filtered.</p>"
            f"<div class='table-wrap'><table><tr><th>rule</th><th>role</th><th>metric</th><th>effect</th><th>row_filter</th><th>deny_dims</th></tr>"
            f"{rule_rows}</table></div></section>"
            f"<section id='sec-playground' data-ws-group='playground'><h2>Interactive governed query &amp; graph runner</h2>"
            f"<p class='sec-sub'>Click a preset below or enter custom parameters to test RBAC, row filters, M:1 joins, fan-out refusal, and compiled SQL through <code>Service</code>.</p>"
            f"<div class='preset-bar'><span class='preset-label'>1-Click Scenarios:</span>{preset_btns}</div>"
            f"<div class='playground-grid'>"
            f"<div class='card'><div class='card-head'><span class='card-title'>Run <code>query_metric</code></span><span class='badge badge-orange'>QuerySpec AST</span></div>"
            f"<form id='form-query' hx-post='/ui/query' hx-target='#runner-output' hx-swap='innerHTML' method='post' action='/ui/query'>"
            f"<div class='form-row'><label>Metric</label><select name='metric'>{metric_opts or '<option value=\"\">(certify a metric first)</option>'}</select></div>"
            f"{role_field_q}"
            f"<div class='form-row'><label>Dimensions</label><input name='dims' placeholder='e.g. method, Customer.region'></div>"
            f"<div class='form-row'><label>Filters</label><input name='filters' placeholder='col=val or JSON {{&quot;method&quot;:{{&quot;in&quot;:[&quot;card&quot;]}}}}'></div>"
            f"<details id='query-adv' class='adv-box'><summary>Advanced: time grain, range, order, limit &amp; entity keys</summary>"
            f"<div class='form-row'><label>Time grain</label><select name='time_grain'>"
            f"<option value=''>none</option><option value='day'>day</option><option value='week'>week (ISO Mon)</option>"
            f"<option value='month'>month</option><option value='quarter'>quarter</option><option value='year'>year</option></select></div>"
            f"<div class='form-row'><label>Time range</label><input name='time_range' placeholder='e.g. last_30_days or 2024-01-01..2024-03-01'></div>"
            f"<div class='form-row'><label>Order / Limit</label><div class='act-inline' style='flex:1'>"
            f"<input name='order_by' placeholder='e.g. -revenue' style='flex:1'>"
            f"<input name='limit' placeholder='limit' style='width:84px'></div></div>"
            f"<div class='form-row'><label>Entity keys</label><input name='entity_keys' placeholder='optional, e.g. 4, 5, 6'></div>"
            f"</details>"
            f"<button class='btn-primary' type='submit'>execute governed metric</button></form></div>"
            f"<div class='card'><div class='card-head'><span class='card-title'>Run <code>traverse_graph</code></span><span class='badge badge-orange'>MCP tool</span></div>"
            f"<form id='form-traverse' hx-post='/ui/traverse' hx-target='#runner-output' hx-swap='innerHTML' method='post' action='/ui/traverse'>"
            f"<div class='form-row'><label>Start entity</label><select name='start'>{entity_opts or '<option value=\"Customer\">Customer</option>'}</select></div>"
            f"<div class='form-row'><label>Rel path</label><input name='path' placeholder='e.g. HAS_ORDER'></div>"
            f"{role_field_t}"
            f"<div class='form-row'><label>Entity filter</label><input name='filters' placeholder='e.g. Order.status=returned'></div>"
            f"<button class='btn-primary' type='submit'>traverse entity keys</button></form>"
            f"<div style='margin-top:16px;padding-top:14px;border-top:1px solid var(--border)'>"
            f"<div class='card-head'><span class='card-title'>Canary Health Probe (<code>LIMIT 0</code>)</span><span class='badge badge-warn'>ADR-031</span></div>"
            f"<form hx-post='/ui/canary' hx-target='#runner-output' hx-swap='innerHTML' method='post' action='/ui/canary' class='act-inline'>"
            f"<label style='font-size:12px;color:var(--text-secondary)'><input type='checkbox' name='dry_run' value='1' checked> dry-run</label>"
            f"<button type='submit'>run canary probe</button></form></div></div>"
            f"</div><div id='runner-output' style='margin-top:14px'></div></section>"
            f"<section id='sec-feedback' data-ws-group='ops'><h2>Drift, feedback &amp; BigQuery Materialized View advisor</h2>"
            f"<div class='card-grid'>"
            f"<div class='card'><div class='card-head'><span class='card-title'>Top metric demand</span><span class='badge'>usage.jsonl</span></div><code>{e(demand_str)}</code></div>"
            f"<div class='card'><div class='card-head'><span class='card-title'>Clustered coverage gaps</span><span class='badge badge-warn'>report_gap</span></div><code>{e(gaps_str)}</code></div>"
            f"<div class='card'><div class='card-head'><span class='card-title'>Flagged metrics (&gt;20% error/issue)</span><span class='badge badge-err'>health</span></div><code>{e(flagged_str)}</code></div>"
            f"</div>{mv_html}</section>"
            f"<section id='sec-audit' data-ws-group='ops'><h2>Audit trail</h2>"
            f"<p class='sec-sub'>Append-only governance log from <code>definitions/_state/audit.jsonl</code> (mirrored to Cloud Logging JSON when <code>--log-json</code> is enabled).</p>"
            f"<div class='table-wrap'><table><tr><th>timestamp</th><th>action</th><th>target</th><th>actor</th><th>version &amp; edit ratio</th></tr>"
            f"{audit_rows}</table></div></section>"
            f"<section id='sec-mcp' data-ws-group='ops'><h2>Agent MCP &amp; HTTP API reference</h2>"
            f"<div class='table-wrap'><table><tr><th>Surface</th><th>Tools / Endpoints</th></tr>"
            f"<tr><td><b class='def-name'>MCP Server (stdio)</b></td><td><code>list_metrics</code> · <code>search_metrics</code> · <code>describe_metric</code> · <code>query_metric</code> · <code>query_metrics</code> · <code>describe_graph</code> · <code>traverse_graph</code> · <code>report_gap</code> · <code>report_issue</code> (no raw SQL tool)</td></tr>"
            f"<tr><td><b class='def-name'>JSON REST API</b></td><td><code>GET /api/metrics</code> · <code>GET /api/queue</code> · <code>POST /api/certify/{{kind}}/{{name}}?owner=...</code></td></tr>"
            f"<tr><td><b class='def-name'>GCP &amp; Local Backends</b></td><td><code>DuckDBWarehouse</code> (bound parameters) · <code>BigQueryMCP</code> (<code>execute_sql_readonly</code> + <code>dryRun</code> + <code>JOBS_BY_PROJECT</code> + Dataplex Policy Tags) · Cloud Run + Secret Manager</td></tr>"
            f"</table></div></section>"
            f"</main></div>{sys_html}<div id='toast' class='toast' role='status' aria-live='polite'></div></body></html>"
        )

    @app.post("/certify/{kind}/{name}", response_class=HTMLResponse)
    def certify(kind: str, name: str, owner: str = Form(""), hx_request: str | None = Header(None)):
        if kind not in KINDS:
            return HTMLResponse(f"unknown kind: {e(kind)}", status_code=400)
        try:
            reg.certify(kind, name, owner=owner.strip() or None, by="ui")
        except (InvalidTransition, KeyError, ValueError) as ex:
            n = reg.get(kind, name, pending=bool(reg.get(kind, name, pending=True))) or reg.get(kind, name)
            if hx_request and n is not None:
                diff_text = reg.diff(kind, name) if n.version else ""
                return _row(n, str(ex), diff_text=diff_text)
            code = 404 if isinstance(ex, KeyError) else 400
            if hx_request:
                return HTMLResponse(
                    f'<tr id="{e(kind)}-{e(name)}"><td colspan=5>{e(str(ex))}</td></tr>',
                    status_code=code,
                )
            return HTMLResponse(e(str(ex)), status_code=code)
        if hx_request:
            return (f'<tr id="{e(kind)}-{e(name)}"><td colspan=5>'
                    f'<span class="badge badge-ok">certified {e(name)}</span></td></tr>')
        return RedirectResponse("/", 303)

    @app.post("/reject/{kind}/{name}", response_class=HTMLResponse)
    def reject(kind: str, name: str, hx_request: str | None = Header(None)):
        if kind not in KINDS:
            return HTMLResponse(f"unknown kind: {e(kind)}", status_code=400)
        try:
            reg.reject(kind, name, by="ui")
        except (InvalidTransition, KeyError, ValueError) as ex:
            code = 404 if isinstance(ex, KeyError) else 400
            if hx_request:
                return HTMLResponse(
                    f'<tr id="{e(kind)}-{e(name)}"><td colspan=5>{e(str(ex))}</td></tr>',
                    status_code=code,
                )
            return HTMLResponse(e(str(ex)), status_code=code)
        if hx_request:
            return (f'<tr id="{e(kind)}-{e(name)}"><td colspan=5>'
                    f'<span class="badge badge-err">rejected {e(name)}</span></td></tr>')
        return RedirectResponse("/", 303)

    @app.post("/conflicts/pick", response_class=HTMLResponse)
    def ui_conflict_pick(winner: str = Form("")):
        cs = detect(reg.list("metric"))
        c = next((x for x in cs if winner in x.variants), None)
        if c is None:
            return HTMLResponse(f"no conflict containing {e(winner)}", status_code=400)
        apply_pick(reg, c, winner, by="ui")
        return RedirectResponse("/#sec-conflicts", 303)

    @app.post("/conflicts/split", response_class=HTMLResponse)
    async def ui_conflict_split(request: Request):
        form = await request.form()
        concept = str(form.get("concept", ""))
        cs = detect(reg.list("metric"))
        c = next((x for x in cs if x.concept == concept and x.kind == "variant"), None)
        if c is None:
            return HTMLResponse(f"no variant conflict for {e(concept)}", status_code=400)
        mapping = {}
        for v in c.variants:
            target = str(form.get(f"rename_{v}", "")).strip()
            if target:
                mapping[v] = target
        try:
            apply_split(reg, c, mapping or None, by="ui")
        except (KeyError, ValueError) as ex:
            return HTMLResponse(e(str(ex)), status_code=400)
        return RedirectResponse("/#sec-queue", 303)

    @app.post("/ui/query", response_class=HTMLResponse)
    def ui_query(
        metric: str = Form(""),
        as_role: str = Form(""),
        dims: str = Form(""),
        filters: str = Form(""),
        entity_keys: str = Form(""),
        time_grain: str = Form(""),
        time_range: str = Form(""),
        order_by: str = Form(""),
        limit: str = Form(""),
    ):
        dim_list = [x.strip() for x in dims.split(",") if x.strip()] or None
        parsed_filters: dict | list | None = None
        raw_flt = filters.strip()
        if raw_flt:
            if raw_flt.startswith(("{", "[")):
                try:
                    parsed_filters = json.loads(raw_flt)
                except json.JSONDecodeError as ex:
                    return _render_query_result(None, f"invalid filter JSON: {ex}")
            else:
                flt_dict: dict = {}
                for item in [x.strip() for x in raw_flt.split(",") if x.strip()]:
                    if "=" not in item:
                        return _render_query_result(None, f"invalid filter '{item}' (use col=value or JSON)")
                    k, _, v = item.partition("=")
                    flt_dict[k.strip()] = v.strip()
                parsed_filters = flt_dict or None
        keys_list = None
        if entity_keys.strip():
            keys_list = []
            for raw in [x.strip() for x in entity_keys.split(",") if x.strip()]:
                keys_list.append(int(raw) if raw.lstrip("-").isdigit() else raw)
        tr_val = None
        if time_range.strip():
            tr_s = time_range.strip()
            tr_val = [p.strip() for p in tr_s.split("..", 1)] if ".." in tr_s else tr_s
        lim_val = None
        if limit.strip():
            if not limit.strip().lstrip("-").isdigit():
                return _render_query_result(None, f"invalid limit '{limit.strip()}' (expected integer)")
            lim_val = int(limit.strip())
        role_arg = "" if svc.bound_role else as_role.strip()
        try:
            res = svc.query_metric(
                metric.strip(),
                dim_list,
                parsed_filters,
                as_role=role_arg,
                entity_keys=keys_list,
                time_grain=time_grain.strip() or None,
                time_range=tr_val,
                order_by=order_by.strip() or None,
                limit=lim_val,
            )
            return _render_query_result(res)
        except Refusal as ex:
            return _render_query_result(None, str(ex))

    @app.post("/ui/traverse", response_class=HTMLResponse)
    def ui_traverse(start: str = Form(""), path: str = Form(""), as_role: str = Form(""),
                    filters: str = Form("")):
        rel_list = [x.strip() for x in path.split(",") if x.strip()]
        flt_dict: dict[str, dict[str, str]] = {}
        for item in [x.strip() for x in filters.split(",") if x.strip()]:
            if "=" not in item or "." not in item.partition("=")[0]:
                return _render_query_result(None, f"invalid entity filter '{item}' (use Entity.col=value)")
            lhs, _, val = item.partition("=")
            ent, _, col = lhs.partition(".")
            flt_dict.setdefault(ent.strip(), {})[col.strip()] = val.strip()
        role_arg = "" if svc.bound_role else as_role.strip()
        try:
            res = svc.traverse_graph(start.strip(), rel_list, flt_dict or None, as_role=role_arg)
            return _render_query_result(res)
        except Refusal as ex:
            return _render_query_result(None, str(ex))

    @app.post("/ui/canary", response_class=HTMLResponse)
    def ui_canary(dry_run: str = Form("")):
        res = run_canary(reg, svc.wh, suspend=not bool(dry_run), by="ui-canary")
        badge = "badge-err" if res["failed"] else "badge-ok"
        label = f"{len(res['failed'])} failed" if res["failed"] else f"{len(res['passed'])} healthy"
        mode = "dry-run" if dry_run else "live"
        return (
            f'<div class="card"><div class="card-head">'
            f'<span class="card-title">Canary Probe ({mode})</span>'
            f'<span class="badge {badge}">{e(label)}</span></div>'
            f'<pre>{e(json.dumps(res, indent=2))}</pre></div>'
        )

    return app
