import crypto from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";

import JSZip from "jszip";
import { FileBlob, Presentation, PresentationFile } from "@oai/artifact-tool";

const workspaceDir = path.resolve(path.dirname(new URL(import.meta.url).pathname), "../..");
const deckDir = path.join(workspaceDir, "presentation", "mid-semester");
const buildDir = path.join(deckDir, ".build");
const outputDir = path.join(deckDir, "output");
const proposalPath = path.join(
  workspaceDir,
  "presentation",
  "proposal",
  "climate-futures-pricing-proposal.pptx",
);
const skillDir = "/Users/haydn/.codex/plugins/cache/openai-primary-runtime/presentations/26.1011.11113/skills/presentations";
const pythonExecutable = "/Users/haydn/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3";
const version = process.argv[2] ?? "v1";
const finalPath = path.join(
  outputDir,
  `climate-futures-pricing-midterm-update-${version}.pptx`,
);

const { applyPresentationChartFont, finalizePresentation } = await import(
  pathToFileURL(path.join(skillDir, "container_tools", "artifact_tool_utils.mjs")).href
);

const C = {
  dark: "#16302A",
  green: "#2E6B4F",
  greenSoft: "#DCEBE2",
  mist: "#BFD3C8",
  gold: "#E1A624",
  goldText: "#9A6D0B",
  red: "#C2502D",
  redSoft: "#F9E7E2",
  ink: "#1D2723",
  muted: "#5E6A64",
  line: "#D6DDD8",
  grid: "#E3E8E5",
  panel: "#F2F5F3",
  white: "#FFFFFF",
  gray: "#AEB7B2",
};

const HEAD = "Georgia";
const BODY = "Arial";
const W = 1280;
const H = 720;
const M = 58;
const CW = W - 2 * M;

function addText(slide, text, position, options = {}) {
  const shape = slide.shapes.add({
    geometry: "textbox",
    position,
    fill: "none",
    line: { style: "solid", fill: "none", width: 0 },
  });
  shape.text = text;
  shape.text.style = {
    typeface: options.typeface ?? BODY,
    fontSize: options.fontSize ?? 20,
    bold: options.bold ?? false,
    italic: options.italic ?? false,
    color: options.color ?? C.ink,
    alignment: options.alignment ?? "left",
    verticalAlignment: options.verticalAlignment ?? "top",
    autoFit: options.autoFit ?? "none",
    insets: options.insets ?? 0,
    lineSpacing: options.lineSpacing ?? 1,
  };
  return shape;
}

function addBox(slide, position, fill = C.panel, options = {}) {
  const geometry = options.geometry ?? "roundRect";
  return slide.shapes.add({
    geometry,
    position,
    fill,
    line: {
      style: "solid",
      fill: options.lineFill ?? fill,
      width: options.lineWidth ?? 1,
    },
    ...(["rect", "textbox", "roundRect"].includes(geometry)
      ? { borderRadius: options.borderRadius ?? "rounded-lg" }
      : {}),
  });
}

function addRule(slide, left, top, width, height = 2, fill = C.line) {
  return slide.shapes.add({
    geometry: "rect",
    position: { left, top, width, height },
    fill,
    line: { style: "solid", fill, width: 0 },
  });
}

function addHeader(slide, title, subtitle, number) {
  addText(slide, title, { left: M, top: 38, width: CW, height: 66 }, {
    typeface: HEAD,
    fontSize: 48,
    bold: true,
    verticalAlignment: "middle",
  });
  addText(slide, subtitle, { left: M, top: 108, width: CW, height: 42 }, {
    fontSize: 24,
    color: C.muted,
    verticalAlignment: "middle",
  });
  addText(slide, String(number), { left: 1174, top: 662, width: 48, height: 28 }, {
    fontSize: 13,
    color: C.muted,
    alignment: "right",
    verticalAlignment: "middle",
  });
}

function addFooter(slide, text) {
  addText(slide, text, { left: M, top: 662, width: 1085, height: 30 }, {
    fontSize: 13,
    color: C.muted,
    verticalAlignment: "middle",
  });
}

function setNotes(slide, minutes, leaveBy, points, sources) {
  slide.speakerNotes.text = [
    `${minutes}  |  leave by ${leaveBy}`,
    "",
    ...points.map((point) => `- ${point}`),
    "",
    `Sources: ${sources}`,
  ].join("\n");
  slide.speakerNotes.setVisible(true);
}

async function loadTemplateIcons() {
  const zip = await JSZip.loadAsync(await fs.readFile(proposalPath));
  const files = {
    corn: "ppt/media/image1.png",
    database: "ppt/media/image10.png",
    bulb: "ppt/media/image12.png",
    map: "ppt/media/image18.png",
    heat: "ppt/media/image19.png",
    chart: "ppt/media/image20.png",
    globe: "ppt/media/image21.png",
    warning: "ppt/media/image23.png",
    arrow: "ppt/media/image24.png",
    check: "ppt/media/image25.png",
    github: "ppt/media/image29.png",
  };
  const icons = {};
  for (const [name, file] of Object.entries(files)) {
    const bytes = await zip.file(file).async("uint8array");
    icons[name] = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength);
  }
  return icons;
}

function addIcon(slide, icon, position, alt) {
  return slide.images.add({
    blob: icon,
    contentType: "image/png",
    alt,
    fit: "contain",
    position,
  });
}

function addIconCircle(slide, icon, left, top, size, fill, alt, padding = 18) {
  addBox(slide, { left, top, width: size, height: size }, fill, {
    geometry: "ellipse",
    borderRadius: 0,
  });
  addIcon(slide, icon, {
    left: left + padding,
    top: top + padding,
    width: size - 2 * padding,
    height: size - 2 * padding,
  }, alt);
}

function addStat(slide, value, label, left, top, color = C.green, width = 260) {
  addText(slide, value, { left, top, width, height: 72 }, {
    fontSize: 58,
    bold: true,
    color,
    verticalAlignment: "middle",
  });
  addText(slide, label, { left, top: top + 75, width, height: 58 }, {
    fontSize: 18,
    color: C.ink,
  });
}

const icons = await loadTemplateIcons();
const presentation = Presentation.create({ slideSize: { width: W, height: H } });

// Slide 1: title
{
  const slide = presentation.slides.add();
  slide.background.fill = C.dark;
  addText(slide, "DUKE AIPI 590  ·  ALTERNATIVE DATA  ·  MID-SEMESTER UPDATE",
    { left: M, top: 138, width: 790, height: 34 }, {
      fontSize: 17, bold: true, color: C.gold, verticalAlignment: "middle",
    });
  addText(slide, "Climate Impacts on\nFutures Pricing",
    { left: M, top: 185, width: 760, height: 188 }, {
      typeface: HEAD, fontSize: 68, bold: true, color: C.white,
      verticalAlignment: "middle", lineSpacing: 0.94,
    });
  addText(slide, "The physical-risk model works. The trading signal did not.",
    { left: M, top: 392, width: 730, height: 82 }, {
      fontSize: 29, color: C.mist, verticalAlignment: "middle",
    });
  addText(slide, "Haydn Stucker  ·  October 14, 2026",
    { left: M, top: 532, width: 700, height: 38 }, {
      fontSize: 20, color: C.white, verticalAlignment: "middle",
    });
  addIconCircle(slide, icons.corn, 900, 148, 288, C.gold, "Corn icon", 58);
  addIconCircle(slide, icons.heat, 830, 420, 122, C.white, "Temperature icon", 28);
  addIconCircle(slide, icons.chart, 1090, 443, 122, C.white, "Market chart icon", 28);
  setNotes(slide, "0:20", "0:20", [
    "Open with the project question: can point-in-time climate data measure crop supply shocks and support a repeatable buy or sell signal?",
    "State the conclusion immediately: the supply-risk model works, while the trading models did not survive validation.",
    "Preview the structure: progress, data, models, results, risks and next steps.",
  ], "README.md; proposal deck");
}

// Slide 2: project overview
{
  const slide = presentation.slides.add();
  addHeader(slide, "Project overview", "One project, two different tests", 2);

  const cards = [
    {
      left: M,
      tag: "MEASURE",
      question: "Can public climate data identify real crop supply shocks?",
      answer: "Compare weather and drought features with held-out crop yields.",
      color: C.green,
      icon: icons.map,
    },
    {
      left: 660,
      tag: "PREDICT",
      question: "Can those signals predict tradeable commodity returns?",
      answer: "Compare climate models with market-only and simple-rule baselines.",
      color: C.red,
      icon: icons.chart,
    },
  ];
  for (const card of cards) {
    addBox(slide, { left: card.left, top: 190, width: 562, height: 350 }, C.panel);
    addIconCircle(slide, card.icon, card.left + 32, 220, 78, C.white, `${card.tag} icon`, 18);
    addText(slide, card.tag, { left: card.left + 130, top: 222, width: 385, height: 42 }, {
      fontSize: 18, bold: true, color: card.color, verticalAlignment: "middle",
    });
    addText(slide, card.question, { left: card.left + 32, top: 310, width: 490, height: 110 }, {
      fontSize: 29, bold: true, color: C.ink, verticalAlignment: "middle",
    });
    addText(slide, card.answer, { left: card.left + 32, top: 445, width: 490, height: 70 }, {
      fontSize: 20, color: C.muted,
    });
  }
  addBox(slide, { left: M, top: 566, width: CW, height: 72 }, C.dark);
  addText(slide,
    "Intended impact: better crop-risk measurement and an honest test of whether public data adds market timing value.",
    { left: 88, top: 578, width: 1104, height: 48 }, {
      typeface: HEAD, fontSize: 24, color: C.white, verticalAlignment: "middle",
    });
  setNotes(slide, "0:45", "1:05", [
    "Restate the original goal as two linked tests.",
    "The physical test asks whether public climate data can identify real crop supply shocks.",
    "The market test asks whether those signals arrive early enough to improve commodity return predictions beyond market-only and simple-rule baselines.",
    "The intended impact is better crop-risk measurement and an honest assessment of whether public data adds market-timing value.",
    "Keep the two tests separate: a failed trading signal does not erase a useful supply-risk result.",
  ], "README.md, executive summary and research questions");
}

// Slide 3: progress
{
  const slide = presentation.slides.add();
  addHeader(slide, "Progress since the proposal", "The project moved from exploratory correlations to execution-grade tests", 3);

  const milestones = [
    ["1", "DATA PIPELINE", "Automated public climate, drought, yield and futures data", C.green],
    ["2", "PHYSICAL MODEL", "County weather and drought validated against held-out corn yields", C.green],
    ["3", "MARKET TESTS", "Dated LSEG contracts, costs, rolls and point-in-time labels", C.green],
    ["4", "STRESS TESTS", "Corn forecasts, USDA releases, cocoa and coffee", C.green],
    ["5", "DECISION", "No trading specification clears the final validation gate", C.red],
  ];
  addRule(slide, 105, 300, 1060, 5, C.line);
  milestones.forEach(([n, tag, body, color], index) => {
    const x = 76 + index * 238;
    addBox(slide, { left: x, top: 248, width: 56, height: 56 }, color, {
      geometry: "ellipse", borderRadius: 0,
    });
    addText(slide, n, { left: x, top: 248, width: 56, height: 56 }, {
      fontSize: 24, bold: true, color: C.white, alignment: "center",
      verticalAlignment: "middle",
    });
    addText(slide, tag, { left: x - 18, top: 336, width: 205, height: 34 }, {
      fontSize: 16, bold: true, color, verticalAlignment: "middle",
    });
    addText(slide, body, { left: x - 18, top: 380, width: 205, height: 125 }, {
      fontSize: 18, color: C.ink,
    });
  });
  addBox(slide, { left: M, top: 552, width: CW, height: 84 }, C.greenSoft);
  addText(slide,
    "Current status: the original trading hypothesis failed a much stronger test than the proposal could support.",
    { left: 88, top: 567, width: 1100, height: 54 }, {
      fontSize: 23, bold: true, color: C.dark, verticalAlignment: "middle",
    });
  setNotes(slide, "0:45", "1:50", [
    "Summarize the progress from left to right: data pipeline, physical model, market tests, stress tests and decision.",
    "The largest change is methodological: exploratory correlations became dated-contract tests with exact information cutoffs and realistic costs.",
    "The analysis expanded from corn to cocoa and coffee, with a fixed validation gate for each market.",
    "The original trading hypothesis failed, but the project now has stronger and more defensible evidence than the proposal stage.",
  ], "README.md development checklist; Git history through PR #8");
}

// Slide 4: data preparation
{
  const slide = presentation.slides.add();
  addHeader(slide, "Data collection and preparation", "Every feature is aligned to when it became available", 4);

  const sources = [
    [icons.globe, "PUBLIC CLIMATE", "NASA POWER, NOAA drought and GEFS forecasts"],
    [icons.corn, "CROP OUTCOMES", "County production, annual yield and USDA progress"],
    [icons.chart, "MARKET DATA", "Dated LSEG corn, wheat, cocoa and coffee contracts"],
  ];
  sources.forEach(([icon, tag, body], index) => {
    const x = M + index * 280;
    addBox(slide, { left: x, top: 188, width: 250, height: 152 }, C.panel);
    addIcon(slide, icon, { left: x + 22, top: 210, width: 50, height: 50 }, `${tag} icon`);
    addText(slide, tag, { left: x + 88, top: 212, width: 140, height: 34 }, {
      fontSize: 15, bold: true, color: C.green, verticalAlignment: "middle",
    });
    addText(slide, body, { left: x + 22, top: 276, width: 206, height: 50 }, {
      fontSize: 17, color: C.ink,
    });
  });
  addBox(slide, { left: 925, top: 188, width: 297, height: 152 }, C.dark);
  addIcon(slide, icons.database, { left: 950, top: 209, width: 52, height: 52 }, "Database icon");
  addText(slide, "POINT-IN-TIME PANEL", { left: 1020, top: 212, width: 175, height: 34 }, {
    fontSize: 15, bold: true, color: C.gold, verticalAlignment: "middle",
  });
  addText(slide, "One decision row with the data actually available at that moment",
    { left: 950, top: 274, width: 244, height: 52 }, {
      fontSize: 17, color: C.white,
    });

  addRule(slide, 180, 367, 925, 3, C.green);
  [295, 575, 855].forEach((x) => addBox(slide, { left: x, top: 355, width: 24, height: 24 }, C.green, {
    geometry: "ellipse", borderRadius: 0,
  }));

  const fixes = [
    ["Publication lag", "Store release timestamps and prohibit future data"],
    ["Missing and uneven coverage", "Keep missing values explicit and impute only inside training"],
    ["Contract rolls and trading costs", "Use dated contracts, next-open entries and row-level costs"],
  ];
  fixes.forEach(([issue, fix], index) => {
    const top = 408 + index * 70;
    addText(slide, issue, { left: M, top, width: 260, height: 46 }, {
      fontSize: 20, bold: true, color: C.red, verticalAlignment: "middle",
    });
    addText(slide, fix, { left: 338, top, width: 884, height: 46 }, {
      fontSize: 20, color: C.ink, verticalAlignment: "middle",
    });
    if (index < fixes.length - 1) addRule(slide, M, top + 57, CW, 1, C.line);
  });
  addFooter(slide, "Sources: NASA POWER, NOAA, USDA NASS, US Drought Monitor and LSEG Workspace.");
  setNotes(slide, "0:55", "2:45", [
    "Identify the four source groups: public climate observations and forecasts, county crop outcomes, dated LSEG futures contracts and the combined point-in-time panel.",
    "The central preparation rule is point-in-time availability: each feature appears only after it would have been known in real time.",
    "Public releases retain their actual timestamps, and market labels begin at the first contract open after publication.",
    "Missing crop stages remain missing. Any imputation is learned only inside the training window to prevent leakage.",
    "Dated LSEG contracts remove continuous-price roll artifacts and support next-open execution with modeled transaction costs.",
    "Limited history and uneven extreme events remain constraints, so later slides use time-based validation and sensitivity tests.",
  ], "docs/data_contracts.md; docs/gefs_forecast_revisions.md; docs/crop_progress_releases.md; README.md");
}

// Slide 5: model development and baseline
{
  const slide = presentation.slides.add();
  addHeader(slide, "Model development", "Climate models had to beat market-only and simple-rule baselines", 5);

  addBox(slide, { left: M, top: 185, width: 350, height: 386 }, C.dark);
  addText(slide, "BASELINE", { left: 88, top: 220, width: 280, height: 34 }, {
    fontSize: 17, bold: true, color: C.gold,
  });
  addText(slide, "Market-only model", { left: 88, top: 275, width: 280, height: 60 }, {
    typeface: HEAD, fontSize: 33, bold: true, color: C.white,
  });
  addText(slide,
    "Recent returns, volatility, curve shape and seasonality, with the same dates and costs as every climate candidate.",
    { left: 88, top: 360, width: 282, height: 130 }, {
      fontSize: 21, color: C.mist,
    });
  addText(slide, "+ fixed trading rules", { left: 88, top: 515, width: 280, height: 34 }, {
    fontSize: 18, bold: true, color: C.white,
  });

  addText(slide, "CANDIDATE FEATURES", { left: 460, top: 185, width: 310, height: 34 }, {
    fontSize: 16, bold: true, color: C.green,
  });
  const features = [
    [icons.heat, "Observed stress", "Heat, rainfall and drought"],
    [icons.globe, "Forecast revisions", "Operational GEFS changes"],
    [icons.corn, "Scheduled releases", "USDA crop condition and progress"],
  ];
  features.forEach(([icon, name, body], index) => {
    const top = 238 + index * 112;
    addIconCircle(slide, icon, 460, top, 66, C.greenSoft, `${name} icon`, 15);
    addText(slide, name, { left: 548, top, width: 295, height: 36 }, {
      fontSize: 21, bold: true, color: C.ink,
    });
    addText(slide, body, { left: 548, top: top + 42, width: 295, height: 34 }, {
      fontSize: 18, color: C.muted,
    });
  });

  addText(slide, "MODEL FAMILIES", { left: 895, top: 185, width: 300, height: 34 }, {
    fontSize: 16, bold: true, color: C.green,
  });
  const models = ["Ridge regression", "Logistic direction model", "Shallow boosted trees"];
  models.forEach((name, index) => {
    const top = 240 + index * 82;
    addBox(slide, { left: 895, top, width: 327, height: 60 }, index === 2 ? C.greenSoft : C.panel);
    addText(slide, name, { left: 920, top: top + 12, width: 280, height: 36 }, {
      fontSize: 19, bold: index === 2, color: C.ink, verticalAlignment: "middle",
    });
  });
  addBox(slide, { left: 895, top: 506, width: 327, height: 65 }, C.redSoft);
  addText(slide, "Selection ended before the final test period", { left: 920, top: 520, width: 280, height: 38 }, {
    fontSize: 18, bold: true, color: C.red, verticalAlignment: "middle",
  });
  addFooter(slide, "All models use expanding time splits, target embargoes, non-overlapping trades and modeled execution costs.");
  setNotes(slide, "0:50", "3:35", [
    "Define the main baseline: a market-only model using recent returns, volatility, curve shape and seasonality, with the same dates and costs as every climate model.",
    "Add simple-rule baselines such as momentum and fixed weather-deterioration rules so the climate models must beat more than a zero-return benchmark.",
    "Candidate climate features include observed heat, rainfall and drought, operational forecast revisions and scheduled USDA releases.",
    "The model families are deliberately compact: Ridge regression, logistic direction models and shallow boosted trees.",
    "Use expanding time splits, target embargoes and non-overlapping trades. Freeze selection before the final period so the model cannot be repaired after seeing the result.",
  ], "analysis/ml_signal_baseline_report.md; config/*validation.yaml; src/pipeline/*validation.py");
}

// Slide 6: physical model result
{
  const slide = presentation.slides.add();
  addHeader(slide, "What worked: measuring corn supply risk", "County weather and drought predict held-out yield shortfalls", 6);

  addText(slide, "Held-out yield R²", { left: M, top: 174, width: 610, height: 34 }, {
    fontSize: 17, bold: true, color: C.green,
  });
  const chart = slide.charts.add("bar", {
    position: { left: M, top: 211, width: 720, height: 396 },
    categories: ["Five weather points", "County averages", "County averages + drought"],
    series: [
      { name: "All years", values: [0.304, 0.613, 0.730], fill: C.green, valuesFormatCode: "0.00" },
      { name: "Excluding 2012", values: [0.166, 0.409, 0.464], fill: C.gold, valuesFormatCode: "0.00" },
    ],
    barOptions: { direction: "bar", grouping: "clustered", gapWidth: 44 },
    hasLegend: true,
    legend: { position: "bottom", overlay: false, textStyle: { fill: C.ink, fontSize: 15 } },
    xAxis: {
      min: 0, max: 0.8, majorUnit: 0.2, numberFormatCode: "0.0",
      textStyle: { fill: C.muted, fontSize: 13 },
      majorGridlines: { style: "solid", fill: C.grid, width: 1 },
      line: { style: "solid", fill: C.line, width: 1 },
    },
    yAxis: {
      textStyle: { fill: C.ink, fontSize: 16 },
      line: { style: "solid", fill: C.line, width: 1 },
    },
    dataLabels: {
      showValue: true, position: "outEnd", numberFormatCode: "0.00",
      textStyle: { fill: C.ink, fontSize: 14, bold: true },
    },
    chartFill: C.white,
    plotAreaFill: C.white,
    chartLine: { style: "solid", fill: C.white, width: 0 },
    plotAreaLine: { style: "solid", fill: C.white, width: 0 },
  });
  applyPresentationChartFont(chart, { fontFamily: BODY });

  addStat(slide, "0.73", "Out-of-sample R² across 26 held-out harvests", 850, 205, C.green, 330);
  addStat(slide, "0.46", "R² after removing the exceptional 2012 drought", 850, 375, C.ink, 330);
  addBox(slide, { left: 836, top: 548, width: 386, height: 72 }, C.greenSoft);
  addText(slide, "The physical measurement generalizes beyond one extreme year",
    { left: 860, top: 561, width: 338, height: 46 }, {
      fontSize: 19, bold: true, color: C.dark, verticalAlignment: "middle",
    });
  addFooter(slide, "Metric: leave-one-year-out R². Source: project corn-yield validation, 2000–2025.");
  setNotes(slide, "0:55", "4:30", [
    "Introduce this as the strongest result in the project: climate and drought data predict held-out county yield shortfalls.",
    "Define the metric: held-out R-squared is the share of variation in unseen yield shortfalls explained by the model. Higher is better, and zero means no improvement over predicting the average.",
    "The proposal used five representative locations and achieved 0.30. County coverage raised held-out R-squared to 0.61.",
    "Adding drought information raised the score to 0.73.",
    "Removing the exceptional 2012 drought still produced 0.46, so the result is not driven by one extreme year.",
    "Conclude that the physical measurement generalizes well enough to support the final supply-risk model.",
  ], "README.md, Results section; analysis/corn_yield_model.py");
}

// Slide 7: trading result
{
  const slide = presentation.slides.add();
  addHeader(slide, "What failed: repeatable buy and sell signals", "Promising development results did not survive stricter validation", 7);

  addText(slide, "Net return after modeled costs", { left: M, top: 174, width: 700, height: 34 }, {
    fontSize: 17, bold: true, color: C.green,
  });
  const chart = slide.charts.add("bar", {
    position: { left: 210, top: 210, width: 548, height: 370 },
    // Horizontal bars render the first category at the bottom, so the data are
    // ordered in reverse to preserve the intended top-to-bottom reading order.
    // PowerPoint renders category labels differently from the preview engine.
    // Keep the chart categories blank and use fixed slide labels below.
    categories: ["\u200B", "\u200B\u200B", "\u200B\u200B\u200B"],
    series: [{
      name: "Net return",
      values: [-0.527, -0.073, 0.420],
      fill: C.gray,
      valuesFormatCode: "0.0%",
      points: [
        { idx: 0, fill: C.red },
        { idx: 1, fill: C.red },
        { idx: 2, fill: C.gold },
      ],
    }],
    barOptions: { direction: "bar", grouping: "clustered", gapWidth: 42, varyColors: true },
    hasLegend: false,
    xAxis: {
      min: -0.6, max: 0.5, majorUnit: 0.2, numberFormatCode: "0%",
      textStyle: { fill: C.muted, fontSize: 13 },
      majorGridlines: { style: "solid", fill: C.grid, width: 1 },
      line: { style: "solid", fill: C.line, width: 1 },
    },
    yAxis: {
      visible: false,
      tickLabelPosition: "none",
      textStyle: { fill: C.white, fontSize: 1 },
      line: { style: "solid", fill: C.white, width: 0 },
    },
    dataLabels: {
      visible: false,
      showValue: false,
    },
    chartFill: C.white,
    plotAreaFill: C.white,
    chartLine: { style: "solid", fill: C.white, width: 0 },
    plotAreaLine: { style: "solid", fill: C.white, width: 0 },
  });
  applyPresentationChartFont(chart, { fontFamily: BODY });

  const chartRows = [
    { label: "GEFS development", value: "42.0%", top: 251, valueLeft: 666, valueWidth: 78, color: C.ink, align: "left" },
    { label: "GEFS 2026 holdout", value: "-7.3%", top: 354, valueLeft: 386, valueWidth: 62, color: C.ink, align: "right" },
    { label: "USDA progress model", value: "-52.7%", top: 457, valueLeft: 330, valueWidth: 100, color: C.white, align: "center" },
  ];
  chartRows.forEach(({ label, value, top, valueLeft, valueWidth, color, align }) => {
    addText(slide, label, { left: M, top, width: 138, height: 44 }, {
      fontSize: 17, color: C.muted, alignment: "right", verticalAlignment: "middle",
    });
    addText(slide, value, { left: valueLeft, top, width: valueWidth, height: 44 }, {
      fontSize: 16, bold: true, color, alignment: align, verticalAlignment: "middle",
    });
  });

  addBox(slide, { left: 805, top: 190, width: 417, height: 132 }, C.dark);
  addText(slide, "CURRENT DECISION", { left: 835, top: 213, width: 350, height: 28 }, {
    fontSize: 15, bold: true, color: C.gold,
  });
  addText(slide, "No buy or sell model is ready for use", { left: 835, top: 254, width: 350, height: 50 }, {
    typeface: HEAD, fontSize: 26, bold: true, color: C.white,
  });

  const metrics = [
    ["−0.98", "GEFS holdout Sharpe"],
    ["3 / 9", "Positive USDA years"],
    ["0", "Strategies promoted"],
  ];
  metrics.forEach(([value, label], index) => {
    const top = 352 + index * 82;
    addText(slide, value, { left: 820, top, width: 130, height: 54 }, {
      fontSize: 35, bold: true, color: index === 2 ? C.red : C.ink,
      verticalAlignment: "middle",
    });
    addText(slide, label, { left: 960, top: top + 5, width: 240, height: 45 }, {
      fontSize: 18, color: C.muted, verticalAlignment: "middle",
    });
  });
  addText(slide, "Evaluation: net after costs, Sharpe, drawdown, positive years, doubled costs and matched baselines",
    { left: M, top: 596, width: 1164, height: 42 }, {
      fontSize: 17, color: C.muted, italic: true, verticalAlignment: "middle",
    });
  addFooter(slide, "Different samples are shown. Replication and gate outcomes matter more than cross-sample magnitude.");
  setNotes(slide, "1:20", "5:50", [
    "State the market conclusion clearly: no tested buy or sell model is ready for use.",
    "The operational GEFS forecast model looked strong in development at plus 42 percent net return and a 1.57 Sharpe ratio.",
    "Define the main metrics: net return subtracts modeled trading costs; Sharpe compares average return with its volatility; drawdown measures the largest peak-to-trough loss; positive years measure consistency.",
    "After the specification was frozen, the 2026 GEFS holdout lost 7.3 percent, produced a negative Sharpe ratio and trailed the market-only and fixed-rule baselines.",
    "The USDA progress model failed before holdout: negative 52.7 percent in development and only three positive years out of nine, so its 2026 holdout remains sealed.",
    "Cocoa and coffee also failed their fixed gates. The decision also checks doubled costs and placebo timing, so adding more algorithms now would become specification search rather than validation.",
  ], "analysis/gefs_corn_validation_report.md; analysis/gefs_corn_holdout_report.md; analysis/nass_corn_progress_validation_report.md; analysis/cocoa_fixed_validation_report.md; analysis/coffee_frost_curve_validation_report.md");
}

// Slide 8: challenges and risks
{
  const slide = presentation.slides.add();
  addHeader(slide, "Challenges, risks and responses", "The main risk is finding a story in noise", 8);
  addText(slide, "RISK", { left: M, top: 172, width: 360, height: 32 }, {
    fontSize: 16, bold: true, color: C.muted,
  });
  addText(slide, "RESPONSE", { left: 620, top: 172, width: 500, height: 32 }, {
    fontSize: 16, bold: true, color: C.muted,
  });
  const rows = [
    ["Many model choices can create false confidence", "Freeze candidates and gates before the final test"],
    ["Extreme weather events are scarce and uneven", "Use time-based validation and report null results"],
    ["Public observations may arrive after prices move", "Track publication times and test operational forecasts"],
  ];
  rows.forEach(([risk, response], index) => {
    const top = 225 + index * 128;
    addBox(slide, { left: M, top, width: 510, height: 96 }, C.redSoft);
    addIcon(slide, icons.warning, { left: 78, top: top + 29, width: 38, height: 38 }, "Risk warning");
    addText(slide, risk, { left: 135, top: top + 21, width: 405, height: 54 }, {
      fontSize: 20, bold: true, color: C.ink, verticalAlignment: "middle",
    });
    addIcon(slide, icons.arrow, { left: 570, top: top + 35, width: 30, height: 24 }, "Response connector");
    addBox(slide, { left: 620, top, width: 602, height: 96 }, C.panel);
    addIcon(slide, icons.check, { left: 642, top: top + 29, width: 38, height: 38 }, "Mitigation check");
    addText(slide, response, { left: 698, top: top + 21, width: 492, height: 54 }, {
      fontSize: 20, color: C.ink, verticalAlignment: "middle",
    });
  });
  addFooter(slide, "A failed predeclared test closes a specification. It does not justify tuning against the failure.");
  setNotes(slide, "0:45", "6:35", [
    "Identify the largest research risk: repeated modeling choices can create false confidence by finding patterns that do not repeat.",
    "Mitigate that risk with frozen candidate sets, predefined gates and sealed holdouts that produce a visible pass or fail decision.",
    "Sparse and uneven extreme-weather events limit what any single market can prove, so use time-based validation and report uncertainty and null results.",
    "Public observations may arrive after prices move, so retain publication timestamps and test operational forecasts rather than revised historical data.",
    "The whitepaper scope prioritizes a defensible conclusion and reproducible evidence over product adoption.",
  ], "config validation protocols; README.md, evidence hierarchy and limitations");
}

// Slide 9: next steps and Q&A
{
  const slide = presentation.slides.add();
  slide.background.fill = C.dark;
  addText(slide, "NEXT STEPS", { left: M, top: 100, width: 400, height: 32 }, {
    fontSize: 17, bold: true, color: C.gold,
  });
  addText(slide, "A supply-risk model is the defensible final deliverable",
    { left: M, top: 150, width: 760, height: 145 }, {
      typeface: HEAD, fontSize: 48, bold: true, color: C.white,
      verticalAlignment: "middle",
    });
  addText(slide,
    "The final project will estimate crop supply risk from point-in-time climate data and document why the tested trading signals do not generalize.",
    { left: M, top: 320, width: 710, height: 112 }, {
      fontSize: 25, color: C.mist,
    });

  const steps = [
    ["1", "Calibrate yield-risk probabilities and uncertainty"],
    ["2", "Package the reproducible pipeline and model card"],
    ["3", "Write the final comparison and document the null result"],
  ];
  steps.forEach(([n, text], index) => {
    const top = 154 + index * 118;
    addBox(slide, { left: 845, top, width: 64, height: 64 }, C.green, {
      geometry: "ellipse", borderRadius: 0,
    });
    addText(slide, n, { left: 845, top, width: 64, height: 64 }, {
      fontSize: 25, bold: true, color: C.white, alignment: "center",
      verticalAlignment: "middle",
    });
    addText(slide, text, { left: 935, top: top - 2, width: 285, height: 70 }, {
      fontSize: 21, bold: true, color: C.white, verticalAlignment: "middle",
    });
  });
  addText(slide, "Questions?", { left: M, top: 518, width: 450, height: 68 }, {
    typeface: HEAD, fontSize: 45, bold: true, color: C.gold,
  });
  addText(slide, "3 minutes", { left: M, top: 590, width: 300, height: 36 }, {
    fontSize: 20, color: C.mist,
  });
  addIcon(slide, icons.github, { left: M, top: 642, width: 30, height: 30 }, "GitHub icon");
  addText(slide, "github.com/haydn-s/climate-futures-pricing",
    { left: 100, top: 641, width: 600, height: 32 }, {
      fontSize: 19, color: C.mist, verticalAlignment: "middle",
    });
  addText(slide, "9", { left: 1174, top: 662, width: 48, height: 28 }, {
    fontSize: 13, color: C.mist, alignment: "right",
  });
  setNotes(slide, "0:25", "7:00", [
    "Close with the pivot: the final deliverable focuses on the crop supply-risk result that generalized.",
    "Next, calibrate yield-risk probabilities, package the reproducible pipeline and model card, and write the cross-market comparison.",
    "The negative trading result remains in the whitepaper as evidence of where public data stopped adding value.",
    "Invite questions and preserve three minutes for Q-and-A.",
  ], "README.md current conclusions and next-phase recommendation");
}

await fs.mkdir(buildDir, { recursive: true });
await fs.mkdir(outputDir, { recursive: true });

const draftRenderDir = path.join(buildDir, `renders-${version}-draft`);
await fs.mkdir(draftRenderDir, { recursive: true });
for (const [index, slide] of presentation.slides.items.entries()) {
  const blob = await presentation.export({ slide, format: "png", scale: 1 });
  await fs.writeFile(
    path.join(draftRenderDir, `slide-${String(index + 1).padStart(2, "0")}.png`),
    new Uint8Array(await blob.arrayBuffer()),
  );
}
const draftMontage = await presentation.export({ format: "webp", montage: true, scale: 0.5 });
await fs.writeFile(
  path.join(draftRenderDir, "montage.webp"),
  new Uint8Array(await draftMontage.arrayBuffer()),
);

const candidatePath = path.join(buildDir, `candidate-${version}.pptx`);
await (await PresentationFile.exportPptx(presentation)).save(candidatePath);

const referenceHash = crypto
  .createHash("sha256")
  .update(await fs.readFile(proposalPath))
  .digest("hex");

const requirements = {
  explicitTotalSlideCount: 9,
  requiredNativeTableOwnerSlides: [],
  requiredNativeChartOwnerSlides: [6, 7],
  requiredEmbeddedWorkbookChartOwnerSlides: [],
  nativeChartTargetApplication: "portable",
  materializeLiteralChartWorkbooks: true,
};
const fontPolicy = {
  basis: "reference",
  families: [HEAD, BODY],
  referencePath: proposalPath,
  referenceSha256: referenceHash,
};

await finalizePresentation({
  ...requirements,
  workspaceDir,
  candidatePath,
  finalPath,
  pythonExecutable,
  integrityValidatorPath: path.join(
    skillDir,
    "container_tools",
    "inspect_presentation_package_integrity.py",
  ),
  layoutValidatorPath: path.join(
    skillDir,
    "container_tools",
    "inspect_presentation_layout_geometry.py",
  ),
  layoutArgs: [
    "--expected-slide-size-emu", "12192000,6858000",
    "--validate-bullet-geometry",
    "--validate-heading-fit",
  ],
  requiredNativeTableOwnerSlides: [],
  fontPolicy,
  verifyArtifactToolImport: true,
  receiptPath: path.join(buildDir, `${path.basename(finalPath)}.validation.json`),
});

const finalDeck = await PresentationFile.importPptx(await FileBlob.load(finalPath));
const finalRenderDir = path.join(buildDir, `renders-${version}-final`);
await fs.mkdir(finalRenderDir, { recursive: true });
for (const [index, slide] of finalDeck.slides.items.entries()) {
  const blob = await finalDeck.export({ slide, format: "png", scale: 1 });
  await fs.writeFile(
    path.join(finalRenderDir, `slide-${String(index + 1).padStart(2, "0")}.png`),
    new Uint8Array(await blob.arrayBuffer()),
  );
}
const finalMontage = await finalDeck.export({ format: "webp", montage: true, scale: 0.5 });
await fs.writeFile(
  path.join(finalRenderDir, "montage.webp"),
  new Uint8Array(await finalMontage.arrayBuffer()),
);

const inspection = await finalDeck.inspect({
  select: "$.slides[*]",
  include: [
    "id", "index", "title", "elements.id", "elements.kind", "elements.text",
    "elements.position", "elements.style", "notes.text",
  ],
});
await fs.writeFile(path.join(buildDir, `inspection-${version}.ndjson`), inspection.ndjson);

console.log(finalPath);
