// Final deck for Climate Futures Pricing.
//   node build_deck.js out.pptx            full deck
//   node build_deck.js outdir --single     one file per slide, for previews
//
// Numbers come from deck_data.json, which deck_data.py derives from the
// committed analysis CSVs, so a slide cannot contradict the repository.
// Figures are the analysis scripts' own SVGs, rasterised here rather than
// redrawn, so a chart cannot drift from the report it came from either.
const fs = require('fs');
const path = require('path');
const pptxgen = require('pptxgenjs');
const JSZip = require('jszip');
const React = require('react');
const ReactDOMServer = require('react-dom/server');
const sharp = require('sharp');
const fa = require('react-icons/fa');
const gi = require('react-icons/gi');

const dataPath = path.join(__dirname, 'deck_data.json');
if (!fs.existsSync(dataPath)) {
  console.error('deck_data.json is missing. Run:  python deck_data.py');
  process.exit(1);
}
const D = JSON.parse(fs.readFileSync(dataPath, 'utf8'));
const analysisDir = path.join(__dirname, '..', '..', 'analysis');

// Palette and type carried over from the proposal and mid-semester decks.
const C = {
  dark: '16302A', green: '2E6B4F', greenSoft: 'DCEBE2', mist: 'BFD3C8',
  gold: 'E1A624', goldText: '9A6D0B', red: 'C2502D', redSoft: 'F9E7E2',
  ink: '1D2723', muted: '5E6A64', line: 'D6DDD8', panel: 'F2F5F3',
  white: 'FFFFFF', gray: 'AEB7B2',
};
const HEAD = 'Georgia';
const BODY = 'Arial';
const W = 13.333;
const M = 0.6;
const CW = W - 2 * M;
const MINUS = '−';

const sign = (v, digits = 3) => (v < 0 ? MINUS : '+') + Math.abs(v).toFixed(digits);
const pct = (v, digits = 0) => (v < 0 ? MINUS : '') + Math.abs(v * 100).toFixed(digits) + '%';
const num = (v, digits = 2) => (v < 0 ? MINUS : '') + Math.abs(v).toFixed(digits);

// ------------------------------------------------------------------ assets
const ICONS = {
  rulerWhite: [fa.FaRulerCombined, C.white], chartWhite: [fa.FaChartLine, C.white],
  warnWhite: [fa.FaExclamationTriangle, C.white], shieldWhite: [fa.FaShieldAlt, C.white],
  scaleWhite: [fa.FaBalanceScale, C.white], searchWhite: [fa.FaSearch, C.white],
  cornGold: [gi.GiCorn, C.gold], githubMist: [fa.FaGithub, C.mist],
  checkGreen: [fa.FaCheck, C.green], crossRed: [fa.FaTimes, C.red],
};
const FIGURES = {
  intervals: 'corn_yield_intervals.svg',
  decisions: 'corn_yield_decisions.svg',
};

async function renderIcons() {
  const out = {};
  for (const [name, [Icon, color]] of Object.entries(ICONS)) {
    const svg = ReactDOMServer
      .renderToStaticMarkup(React.createElement(Icon, { size: '512' }))
      .replace(/currentColor/g, '#' + color);
    const png = await sharp(Buffer.from(svg)).resize(512, 512).png().toBuffer();
    out[name] = 'image/png;base64,' + png.toString('base64');
  }
  return out;
}

// Rasterise the analysis figures rather than redrawing them, so a chart cannot
// drift from the report it came from. They are matplotlib SVGs written with
// svg.fonttype 'none', which librsvg renders faithfully.
//
// A full-width figure occupies about 12 inches on a 13.333-inch slide, so 2400
// pixels is roughly 200 dpi: crisp on a projector and at a 2x zoom, without
// carrying a 6000-pixel bitmap no display will ever resolve.
const FIGURE_WIDTH_PX = 2400;

async function renderFigures() {
  const out = {};
  for (const [name, file] of Object.entries(FIGURES)) {
    const source = path.join(analysisDir, file);
    if (!fs.existsSync(source)) {
      throw new Error(`missing figure ${source}; run the analysis first`);
    }
    const svg = fs.readFileSync(source);
    const meta = await sharp(svg).metadata();
    // Rasterise above the target, then downsample: librsvg renders text at the
    // requested density, so going straight to 2400 would thin the glyphs.
    const png = await sharp(svg, { density: 300 })
      .resize({ width: FIGURE_WIDTH_PX, withoutEnlargement: true })
      .png({ compressionLevel: 9 })
      .toBuffer();
    out[name] = {
      data: 'image/png;base64,' + png.toString('base64'),
      ratio: meta.width / meta.height,
    };
  }
  return out;
}

// ------------------------------------------------------------------ kit
function kit(pres) {
  const text = (slide, str, o) => slide.addText(str, Object.assign(
    { fontFace: BODY, fontSize: 14, color: C.ink, margin: 0, valign: 'top', isTextBox: true }, o));
  return {
    text,
    header(slide, title, subtitle) {
      text(slide, title, { x: M, y: 0.42, w: CW, h: 0.72, fontFace: HEAD, fontSize: 34, bold: true, valign: 'middle' });
      text(slide, subtitle, { x: M, y: 1.14, w: CW, h: 0.44, fontSize: 17, color: C.muted, valign: 'middle' });
    },
    footer(slide, str) {
      text(slide, str, { x: M, y: 6.92, w: 11.4, h: 0.3, fontSize: 10, color: C.muted, valign: 'middle' });
    },
    number(slide, dark = false) {
      slide.slideNumber = { x: 12.23, y: 6.9, w: 0.5, h: 0.3, fontFace: BODY, fontSize: 10, color: dark ? C.mist : C.muted, align: 'right' };
    },
    circle(slide, x, y, d, color) {
      slide.addShape(pres.shapes.OVAL, { x, y, w: d, h: d, fill: { color }, line: { color, width: 0.75 } });
    },
    iconCircle(slide, icon, x, y, d, fill, pad = 0.26) {
      this.circle(slide, x, y, d, fill);
      const p = d * pad;
      slide.addImage({ data: icon, x: x + p, y: y + p, w: d - 2 * p, h: d - 2 * p });
    },
    card(slide, x, y, w, h, fill = C.panel) {
      slide.addShape(pres.shapes.ROUNDED_RECTANGLE, { x, y, w, h, fill: { color: fill }, line: { color: fill, width: 0.75 }, rectRadius: 0.08 });
    },
    // A big number with a caption under it, the deck's main statistical unit.
    stat(slide, x, y, w, value, caption, color = C.green, size = 40) {
      text(slide, value, { x, y, w, h: 0.66, fontFace: BODY, fontSize: size, bold: true, color, align: 'center', valign: 'middle' });
      text(slide, caption, { x, y: y + 0.68, w, h: 0.58, fontSize: 14, color: C.muted, align: 'center', valign: 'top' });
    },
    // Fit a figure inside a box, preserving its aspect ratio.
    figure(slide, fig, box) {
      const h = Math.min(box.h, box.w / fig.ratio);
      const w = h * fig.ratio;
      slide.addImage({ data: fig.data, x: box.x + (box.w - w) / 2, y: box.y + (box.h - h) / 2, w, h });
    },
  };
}

// ------------------------------------------------------------------ delivery plan
// Eight minutes of speaking leaves two minutes for questions inside the hard stop.
// The same plan drives order, notes and the build-time timing check.
const PLAN = [
  ['title', 15], ['recap', 30], ['product', 50], ['data', 55],
  ['models', 50], ['measurement', 50], ['uncertainty', 45], ['nulls', 40],
  ['provisioning', 45], ['risks', 50], ['deployment', 25], ['close', 25],
];
const ORDER = PLAN.map(([key]) => key);
const SPEAKING_SECONDS = 8 * 60;
const QUESTION_SECONDS = 2 * 60;
const clock = seconds => `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;

// Short cues first. Sources and optional answers stay off the submitted slides.
const NOTECARDS = {
  title: `Haydn Stucker, AIPI 590 final, November 18, 2026.
Public weather data can measure corn supply risk. The tested trading signals failed.`,

  recap: `Problem: weather shocks put crop supply and buyers' budgets at risk.
Original users: commodity analysts testing whether public climate data adds timing value.
Final focus: corn buyers, feed mills, ethanol plants and lenders already exposed to supply risk.
The intended impact is a better estimate of shortfall and uncertainty, with honest limits on use.
Sources: README.md, research questions; models/corn-supply-risk-counties-drought-v1/MODEL_CARD.md.`,

  product: `What exists: reproducible Python analysis, committed CSV forecasts and reports, a fixed model specification and model card. This is a research package, not a deployed application.
The October 14 midsemester deck already reported the trading failures. Do not present those as new.
Since then: chronological validation, earlier-data availability audit, uncertainty intervals, and a price-free buyer decision experiment.
The original leave-one-year-out R² and the new R² versus trend use different folds and denominators. This is stronger evaluation, not a comparable score increase.
Sources: presentation/mid-semester/output/Midterm Presentation.pptx, slides 6-9; analysis/corn_yield_forward_validation_report.md; analysis/corn_yield_intervals_report.md; analysis/corn_yield_decisions_report.md.`,

  data: `Weather: NOAA nClimGrid for 473 counties across five states; NASA POWER for five representative points; US Drought Monitor for drought.
Targets: OWID/FAO annual national yield. USDA NASS provides progress releases and county production data, but production weights remain unimplemented.
Trading inputs: NOAA CPC/GEFS forecasts, original USDA releases and dated LSEG futures.
Preparation: aggregate to crop season and geography, retain explicit missingness, fit transforms only on earlier training data, align publication timestamps and next-open execution.
The headline county model uses revised full-season history. The archive does not prove original-vintage availability.
No synthetic training observations. Bootstraps resample historical harvests for uncertainty; the provisioning experiment uses a hypothetical cost function, not invented observed outcomes.
Sources: README.md, Data inventory; analysis/corn_yield_forward_validation_report.md; docs/gefs_forecast_revisions.md; docs/crop_progress_releases.md.`,

  models: `Supply model: national yield deviation from a training-only linear trend. Standardized Ridge with alpha=1 stays fixed from the original study, appropriate to a small annual sample.
Headline inputs: July heat degree-days above 29 C, mean July maximum temperature and peak D2-or-worse drought coverage. Compare county inputs to five points on matched years.
Baseline: fit the same yield trend without weather. Start after ten training years and refit trend and scaling in every expanding fold.
Trading comparison: Ridge, logistic direction and shallow boosted trees, with hyperparameters selected inside earlier time folds. Beat market-only and simple-rule controls on matched costs and dates. Freeze before final testing.
Sources: models/corn-supply-risk-counties-drought-v1/MODEL_CARD.md; src/pipeline/yield_validation.py; docs/corn_wheat_final_validation_protocol.md; analysis/gefs_corn_holdout_report.md; analysis/nass_corn_progress_validation_report.md.`,

  measurement: `Success means lower held-out error than the trend-only control.
R² versus trend = 1 - sum(model error squared) / sum(trend error squared).
Forward R² ${sign(D.supply_risk.r2_all_years)} across ${D.supply_risk.test_years} harvests, or ${sign(D.supply_risk.r2_excluding_2012)} with 2012 removed from both training and testing.
MAE ${num(D.supply_risk.mae_all_years, 3)} versus ${num(D.supply_risk.trend_mae_all_years, 3)} t/ha. Same years and fixed specification.
No single omitted scoring year flips the result: ${sign(D.supply_risk.influence_low)} to ${sign(D.supply_risk.influence_high)}. This differs from refitting without 2012.
Sources: analysis/corn_yield_forward_validation_results.csv; analysis/corn_yield_strata_influence_summary.csv.
IF ASKED: USDA's own forecast is a stronger future benchmark. This benchmark isolates the value of weather against a forecast requiring no weather data.`,

  uncertainty: `The output now includes uncertainty, beyond the midsemester point estimates.
Jackknife+ refits within earlier training years only. The point forecast remains the fixed specification.
${D.uncertainty.covered}/${D.uncertainty.harvests} covered at nominal ${pct(D.uncertainty.nominal_level)}, with average half-width ${num(D.uncertainty.half_width_pp, 1)}% of trend.
2012 clears the bound by only ${num(D.uncertainty.tightest_margin_t_per_ha, 3)} t/ha. The August point-weather nowcast covers ${D.uncertainty.nowcast_covered}/${D.uncertainty.nowcast_harvests} and misses ${D.uncertainty.nowcast_uncovered}.
The theorem's ${pct(D.uncertainty.guaranteed_level)} lower bound requires exchangeability; an ordered yield series does not establish it. Report empirical coverage, not a future coverage guarantee.
Sources: analysis/corn_yield_intervals_coverage.csv; analysis/corn_yield_intervals_predictions.csv; analysis/corn_yield_intervals_report.md.
IF ASKED: sixteen harvests mean one miss changes coverage by 6.25 percentage points. A new harvest is the next independent test.`,

  nulls: `Trading success required positive results after costs, stability, matched controls and frozen gates.
GEFS: ${pct(D.trading_nulls.gefs_holdout_net, 1)} net and Sharpe ${num(D.trading_nulls.gefs_holdout_sharpe)} on ${D.trading_nulls.gefs_holdout_trades} sealed 2026 trades.
USDA: ${pct(D.trading_nulls.usda_net, 1)} net, Sharpe ${num(D.trading_nulls.usda_sharpe)}, positive in ${D.trading_nulls.usda_positive_years}/${D.trading_nulls.usda_years} development years. Seven checks failed, so its 2026 holdout stayed sealed.
These were already midsemester findings. The new explanation is a harvest information comparison: price R² ${num(D.efficiency.price_r2, 3)}, weather increment ${sign(D.efficiency.incremental_r2)}, or ${sign(D.efficiency.incremental_r2_excluding_2012)} without 2012.
That is consistent with overlapping information, not proof of universal market efficiency. ${D.efficiency.observations_available} independent returns cannot rule out a small edge, but these candidates failed the deployment gate.
Sources: analysis/gefs_corn_holdout_results.csv; analysis/nass_corn_progress_validation_results.csv; analysis/corn_price_information_race.csv; analysis/corn_price_information_budget.csv.`,

  provisioning: `Worked product example: a corn buyer uses the August 1 point-weather-and-drought distribution to choose a buffer before the harvest.
At a stated ${D.provisioning.penalty_ratio}:1 shortfall penalty, cost falls ${pct(D.provisioning.saving_all_years)} against climatology; without 2012, ${pct(D.provisioning.saving_excluding_2012)}.
This uses the timely point-weather variant, not the headline full-season county model. Costs are simulated holding-cost units, not realized dollars or trading profits.
The model's spread is ${num(D.provisioning.spread_ratio)} times the baseline's; it does not win simply by widening every buffer.
The bootstrap includes zero at this penalty even with all years, and without 2012 P(saving > 0) is ${pct(D.provisioning.probability_positive_excluding_2012)}. Evidence for a pilot, not established commercial savings.
Adoption hypothesis: an analyst-reviewed risk report for physical buyers. Willingness to pay and operational benefits remain untested.
Sources: analysis/corn_yield_decisions_scores.csv; analysis/corn_yield_decisions_bootstrap.csv; analysis/corn_yield_decisions_spreads.csv; analysis/corn_yield_decisions_report.md.`,

  risks: `Bias: equal weighting across five states is not national production weighting. Test production weights and errors by region before extending to farms or other crops.
Failure case: skill is ${sign(D.supply_risk.severity_high_r2)} in the most extreme third, but ${sign(D.supply_risk.severity_low_r2_ex2012)} in calm seasons without 2012. An aggregate score hides failures.
Availability: current-season county inputs passed the historical availability audit in only 3/26 seasons. Retrospective data may be revised; prospective frozen snapshots are needed.
Responsible use: human review, explicit uncertainty and model-card use limits. No futures position or automated procurement decision based on this prototype.
Gaps: no independent yield holdout, storage economics, basis exposure or contracted delivery constraints. Coffee/cocoa require separate crop-specific validation.
Sources: models/corn-supply-risk-counties-drought-v1/MODEL_CARD.md; analysis/corn_yield_strata_buckets.csv; analysis/corn_yield_nowcast_report.md.`,

  deployment: `Implemented stack: Python, pandas, scikit-learn and Parquet, with CLI ingestion and reproducible CSV/report outputs. There is no production service.
Proposed workflow: scheduled ingestion, freshness checks, a versioned forecast and interval, then analyst review in a procurement report.
Weather is public, but ingestion, storage, maintenance and any licensed market data have costs. No latency or throughput service level has been measured.
Scale cached ingestion by region; new crops need new features and validation. Withhold output on stale inputs or failed validation.
Sources: requirements.txt; src/pipeline/cli.py; src/pipeline/storage.py; README.md, Reproduce; docs/lseg_extraction_checklist.md.`,

  close: `Final outcome: a reproducible corn supply-risk research package with uncertainty and explicit limits. The tested trading strategies failed.
Reflection: predicting physical damage does not establish incremental market information; timing and decision context change what useful means.
Next: freeze a prospective harvest, compare with USDA forecasts and production weights, then pilot a report with physical buyers and realistic storage/basis costs.
Value: make supply exposure easier to reason about without promising a trading edge.
Stop speaking at 8:00. Two minutes remain for questions; hard stop at 10:00.
Q&A: baseline/models on slide 5; 2012 and chronological evaluation on slide 6; calibration on slide 7; market efficiency on slide 8; dollar savings on slide 9.
Sources: models/corn-supply-risk-counties-drought-v1/MODEL_CARD.md; analysis/corn_price_information_report.md; analysis/corn_yield_decisions_report.md.`,
};

// ------------------------------------------------------------------ slides
// Keep the existing palette and type. New content uses aligned text rows;
// citations stay in notes, preserving the local edit that removed footers.
function rows(K, s, items, { y = 1.95, step = 1.14, labelWidth = 3.1, fontSize = 18 } = {}) {
  items.forEach(([label, body], i) => {
    K.text(s, label, { x: M, y: y + i * step, w: labelWidth, h: step - 0.15,
      fontSize, bold: true, color: C.green });
    K.text(s, body, { x: M + labelWidth + 0.3, y: y + i * step,
      w: CW - labelWidth - 0.3, h: step - 0.15, fontSize, color: C.ink,
      lineSpacingMultiple: 1.15 });
  });
}
const SLIDE_FNS = {
  title: (pres, K, I) => {
    const s = pres.addSlide();
    s.background = { color: C.dark };
    K.text(s, 'DUKE AIPI 590 · ALTERNATIVE DATA · FINAL', { x: M, y: 1.5, w: 9, h: 0.35, fontSize: 13, bold: true, color: C.gold, charSpacing: 2 });
    K.text(s, 'Climate Impacts on\nFutures Pricing', { x: M, y: 2.0, w: 9, h: 1.9, fontFace: HEAD, fontSize: 48, bold: true, color: C.white });
    K.text(s, 'Measuring corn supply risk\nand testing public weather signals', { x: M, y: 4.1, w: 8.8, h: 0.9, fontSize: 21, color: C.mist });
    s.addImage({ data: I.cornGold, x: 11, y: 2.1, w: 1.5, h: 1.5 });
    K.text(s, 'Haydn Stucker · November 18, 2026', { x: M, y: 5.55, w: 9, h: 0.4, fontSize: 15, color: C.mist });
    return s;
  },
  recap: (pres, K) => {
    const s = pres.addSlide();
    K.header(s, 'The problem and the people it affects', 'Weather shocks create supply uncertainty for agricultural markets');
    rows(K, s, [
      ['Problem', 'Can public weather data measure crop shortfalls and add information beyond futures prices?'],
      ['Target users', 'Commodity analysts first; corn buyers, feed mills, ethanol plants and lenders for the supply-risk result.'],
      ['Why it matters', 'A shortfall can leave buyers under-provisioned. A false trading signal can turn a plausible story into losses.'],
      ['Intended impact', 'Estimate physical exposure with uncertainty and test whether the same data supports market timing.'],
    ], { step: 1.13, fontSize: 19 });
    return s;
  },
  product: (pres, K) => {
    const s = pres.addSlide();
    K.header(s, 'The final research product', 'A reproducible supply-risk package with a fixed model specification and model card');
    K.text(s, 'AT MIDSEMESTER', { x: M, y: 1.9, w: 5.7, h: 0.35, fontSize: 15, bold: true, color: C.muted });
    K.text(s, 'ADDED FOR THE FINAL', { x: 6.9, y: 1.9, w: 5.8, h: 0.35, fontSize: 15, bold: true, color: C.green });
    const pairs = [
      ['Leave-one-year-out yield estimates', 'Chronological forecasts against a trend baseline'],
      ['Point estimates of crop damage', 'Intervals, coverage and failure-case analysis'],
      ['Supply-risk deliverable proposed', 'Buyer buffer experiment and documented use limits'],
    ];
    pairs.forEach(([before, after], i) => {
      const y = 2.55 + i * 1.05;
      K.text(s, before, { x: M, y, w: 5.55, h: 0.8, fontSize: 22, color: C.muted });
      K.text(s, after, { x: 6.9, y, w: 5.7, h: 0.8, fontSize: 22, color: C.ink });
    });
    K.text(s, 'Delivered: Python pipeline, CSV forecasts, analysis reports and a model card.\nThe trading failures were already known at midsemester.', { x: M, y: 5.9, w: CW, h: 0.7, fontSize: 16, color: C.green });
    return s;
  },
  data: (pres, K) => {
    const s = pres.addSlide();
    K.header(s, 'Data sources and preparation', 'Match the crop geography, the season and the information available at each decision');
    rows(K, s, [
      ['Weather and drought', 'NOAA nClimGrid county weather, NASA POWER points and US Drought Monitor drought coverage.'],
      ['Yield and market data', 'OWID/FAO annual yield; USDA crop releases; NOAA CPC/GEFS forecasts; dated LSEG futures.'],
      ['Preparation', 'Aggregate to growing seasons. Fit trends and scaling inside training folds. Use release times and next-open trades.'],
      ['Key limitation', 'County history is revised and often late. Five-state equal weights do not represent national production weights.'],
    ], { step: 1.03, fontSize: 18 });
    K.text(s, 'No synthetic training data. Resampling estimates uncertainty; the buyer example simulates costs.', { x: M, y: 6.35, w: CW, h: 0.45, fontSize: 15, color: C.muted });
    return s;
  },
  models: (pres, K) => {
    const s = pres.addSlide();
    K.header(s, 'Baselines and model selection', 'The climate model must improve on a matched comparison with less information');
    rows(K, s, [
      ['Supply baseline', 'A linear yield trend, fitted only on earlier harvests. Compare errors on identical test years.'],
      ['Supply model', 'Standardized Ridge, alpha = 1, fixed from the original study. July heat and peak drought explain yield deviations.'],
      ['Forward evaluation', 'Start with ten training harvests. Refit trend and scaling each year. Report R² versus trend, MAE and interval coverage.'],
      ['Trading models', 'Ridge, logistic regression and shallow trees. Select within earlier folds, then freeze. Compare with market-only and fixed rules.'],
    ], { step: 1.14, fontSize: 18 });
    return s;
  },
  measurement: (pres, K) => {
    const s = pres.addSlide();
    K.header(s, 'Corn weather improves the yield estimate', 'Chronological evaluation with full-season county weather and drought');
    const d = D.supply_risk;
    [
      [sign(d.r2_all_years), `R² versus trend across ${d.test_years} harvests`, C.green],
      [sign(d.r2_excluding_2012), 'R² after removing 2012 and refitting', C.green],
      [`${num(d.mae_all_years)} t/ha`, `MAE versus ${num(d.trend_mae_all_years)} for the trend baseline`, C.ink],
    ].forEach(([value, caption, color], i) => {
      const w = (CW - 0.8) / 3;
      K.stat(s, M + i * (w + 0.4), 2.3, w, value, caption, color, 37);
    });
    K.text(s, 'Improvement since midsemester: each test now uses only earlier training years, with a matched trend baseline.', { x: M, y: 4.25, w: CW, h: 0.8, fontSize: 22, color: C.green });
    K.text(s, 'The old leave-one-year-out R² uses a different denominator. These scores are not a before/after accuracy comparison.', { x: M, y: 5.25, w: CW, h: 0.7, fontSize: 18, color: C.muted });
    K.text(s, 'Historical hindcast using revised inputs. No untouched yield holdout or timely county forecast is established.', { x: M, y: 6.25, w: CW, h: 0.5, fontSize: 16, color: C.red });
    return s;
  },
  uncertainty: (pres, K, I, F) => {
    const s = pres.addSlide();
    K.header(s, 'The estimate now includes uncertainty', 'The interval must cover bad seasons as well as ordinary ones');
    K.figure(s, F.intervals, { x: M, y: 1.7, w: CW, h: 3.3 });
    const d = D.uncertainty;
    [
      [`${d.covered}/${d.harvests}`, `historical harvests inside the nominal ${pct(d.nominal_level)} interval`, C.green],
      [`±${num(d.half_width_pp, 1)}%`, 'average half-width as a share of trend yield', C.ink],
      [`${num(d.tightest_margin_t_per_ha, 3)} t/ha`, `margin for the ${d.tightest_year} drought harvest`, C.red],
    ].forEach(([value, caption, color], i) => {
      const w = (CW - 0.8) / 3;
      K.stat(s, M + i * (w + 0.4), 5.1, w, value, caption, color, 26);
    });
    K.text(s, 'Observed coverage is not a future guarantee. The time series does not establish exchangeability.', { x: M, y: 6.5, w: CW, h: 0.35, fontSize: 14, color: C.muted });
    return s;
  },
  nulls: (pres, K) => {
    const s = pres.addSlide();
    K.header(s, 'The trading candidates failed validation', 'Success required positive net returns, stability and stronger results than matched controls');
    const d = D.trading_nulls;
    [
      ['GEFS forecast revisions', `${pct(d.gefs_holdout_net, 1)} net`, `Sharpe ${num(d.gefs_holdout_sharpe)}\n${d.gefs_holdout_trades} trades in the sealed 2026 holdout`],
      ['USDA Crop Progress', `${pct(d.usda_net, 1)} net`, `Sharpe ${num(d.usda_sharpe)}\nPositive in ${d.usda_positive_years} of ${d.usda_years} development years`],
    ].forEach(([name, net, detail], i) => {
      const y = 2.0 + i * 1.35;
      K.text(s, name, { x: M, y, w: 4.4, h: 0.8, fontSize: 22, bold: true });
      K.text(s, net, { x: 5.2, y, w: 2.7, h: 0.8, fontSize: 28, bold: true, color: C.red });
      K.text(s, detail, { x: 8.2, y, w: 4.5, h: 0.9, fontSize: 17, color: C.muted });
    });
    K.text(s, 'Seven USDA checks failed. Its 2026 holdout stayed sealed.\nFrozen candidates were not retuned after failure.', { x: M, y: 4.85, w: CW, h: 0.9, fontSize: 21, color: C.green });
    K.text(s, `New insight: weather adds ${sign(D.efficiency.incremental_r2)} harvest R² beyond December futures prices,\nbut only ${sign(D.efficiency.incremental_r2_excluding_2012)} without 2012. This suggests overlapping information.`, { x: M, y: 6.05, w: CW, h: 0.65, fontSize: 17, color: C.muted });
    return s;
  },
  provisioning: (pres, K, I, F) => {
    const s = pres.addSlide();
    K.header(s, 'A buyer can use the estimate to size a buffer', 'Worked example: an August 1 estimate from point weather and drought, at a 4:1 shortfall penalty');
    K.figure(s, F.decisions, { x: M, y: 1.7, w: CW, h: 3.2 });
    const d = D.provisioning;
    [
      [pct(d.saving_all_years), 'lower simulated cost than a no-weather baseline', C.green],
      [pct(d.saving_excluding_2012), 'estimated saving without 2012; uncertainty includes zero', C.goldText],
      ['Pilot first', 'analyst-reviewed risk reports for physical corn buyers', C.ink],
    ].forEach(([value, caption, color], i) => {
      const w = (CW - 0.8) / 3;
      K.stat(s, M + i * (w + 0.4), 5.0, w, value, caption, color, 28);
    });
    K.text(s, 'Hypothetical cost units, not realized dollars. Storage, basis and contracts are omitted; adoption is untested.', { x: M, y: 6.45, w: CW, h: 0.4, fontSize: 14, color: C.muted });
    return s;
  },
  risks: (pres, K) => {
    const s = pres.addSlide();
    K.header(s, 'Risks, failure cases and responsible use', 'The model card limits the claims and the decisions this prototype can support');
    rows(K, s, [
      ['Geographic bias', 'Five states with equal weights. Test production weights and regional errors before extending to farms or other crops.'],
      ['Uneven performance', `Calm-season R² is ${sign(D.supply_risk.severity_low_r2_ex2012)} without 2012. Show results by season and retain the trend-only fallback.`],
      ['Late or revised data', 'County inputs were timely in only 3 of 26 historical seasons. Archive future vintages and withhold stale outputs.'],
      ['Decision safeguards', 'Human review and explicit intervals. No automated trading or procurement. Validate real costs and a new harvest first.'],
    ], { step: 1.14, fontSize: 18 });
    return s;
  },
  deployment: (pres, K) => {
    const s = pres.addSlide();
    K.header(s, 'Real-world feasibility', 'The pipeline exists; production operation and commercial adoption remain future work');
    rows(K, s, [
      ['Implemented stack', 'Python, pandas, scikit-learn and Parquet. CLI ingestion produces reproducible forecasts and reports.'],
      ['Proposed integration', 'Scheduled ingestion and freshness checks feed a versioned risk report for a procurement analyst.'],
      ['Operational constraints', 'Data-release latency and revisions limit reliability. Public weather still needs maintenance; LSEG requires licensed access.'],
      ['Scaling', 'Cache ingestion by region, monitor missing data, then validate each new crop. No production cost or speed benchmark yet.'],
    ], { step: 1.14, fontSize: 18 });
    return s;
  },
  close: (pres, K, I) => {
    const s = pres.addSlide();
    s.background = { color: C.dark };
    K.text(s, 'OUTCOME AND REFLECTION', { x: M, y: 0.65, w: CW, h: 0.4, fontSize: 14, bold: true, color: C.gold, charSpacing: 2 });
    K.text(s, 'Public weather measures corn supply risk.\nThe tested trading signals failed.', { x: M, y: 1.35, w: CW, h: 1.4, fontFace: HEAD, fontSize: 34, bold: true, color: C.white });
    K.text(s, 'The lesson: useful physical information needs timely data and a validated decision before it creates value.', { x: M, y: 3.0, w: CW, h: 0.9, fontSize: 21, color: C.mist });
    K.text(s, 'WHAT COMES NEXT', { x: M, y: 4.2, w: CW, h: 0.4, fontSize: 14, bold: true, color: C.gold, charSpacing: 2 });
    K.text(s, 'Freeze a new harvest test and compare with USDA forecasts.\nAdd production weights and pilot a buyer report with realistic costs.', { x: M, y: 4.85, w: CW, h: 1.0, fontSize: 22, color: C.white });
    K.text(s, 'Questions?', { x: M, y: 6.2, w: 3.2, h: 0.5, fontSize: 24, color: C.gold, bold: true });
    K.text(s, 'github.com/haydn-s/climate-futures-pricing', { x: 6.5, y: 6.3, w: 6.2, h: 0.35, fontSize: 14, color: C.mist });
    return s;
  },
};

// ------------------------------------------------------------------ build
async function writeDeck(file, keys, I, F) {
  const pres = new pptxgen();
  pres.layout = 'LAYOUT_WIDE';
  pres.title = 'Climate Futures Pricing';
  pres.subject = 'Duke AIPI 590 final presentation';
  pres.author = 'Haydn Stucker';
  const K = kit(pres);
  keys.forEach((key, i) => {
    const s = SLIDE_FNS[key](pres, K, I, F);
    const index = ORDER.indexOf(key);
    if (index > 0) K.number(s, key === 'close');
    const seconds = PLAN[index][1];
    const leaveBy = PLAN.slice(0, index + 1).reduce((sum, [, duration]) => sum + duration, 0);
    s.addNotes(`${clock(seconds)} speaking · leave by ${clock(leaveBy)}\n\n${NOTECARDS[key]}`);
  });
  fs.mkdirSync(path.dirname(file), { recursive: true });
  await pres.writeFile({ fileName: file });
  // PptxGenJS 4 can declare one slide master per slide while emitting only
  // the shared master. Remove only those absent master declarations, leaving
  // real parts and relationships untouched so the package stays valid.
  const zip = await JSZip.loadAsync(fs.readFileSync(file));
  const types = await zip.file('[Content_Types].xml').async('string');
  const cleaned = types.replace(/<Override\b[^>]*\/>/g, entry => {
    const part = entry.match(/PartName="\/(ppt\/slideMasters\/slideMaster\d+\.xml)"/);
    return part && !zip.file(part[1]) ? '' : entry;
  });
  if (cleaned !== types) {
    zip.file('[Content_Types].xml', cleaned);
    fs.writeFileSync(file, await zip.generateAsync({ type: 'nodebuffer', compression: 'DEFLATE' }));
  }
}

(async () => {
  const missing = ORDER.filter((key) => !SLIDE_FNS[key] || !NOTECARDS[key]);
  if (missing.length) throw new Error(`ORDER names a slide or notecard that does not exist: ${missing}`);
  if (new Set(ORDER).size !== ORDER.length) throw new Error('duplicate key in ORDER');
  if (ORDER.length !== Object.keys(SLIDE_FNS).length) throw new Error('every slide must appear in ORDER exactly once');
  const total = PLAN.reduce((sum, [, seconds]) => sum + seconds, 0);
  if (total !== SPEAKING_SECONDS || total + QUESTION_SECONDS > 600) {
    throw new Error('speaking plan must be eight minutes with questions inside the ten-minute hard stop');
  }
  const [target, mode] = process.argv.slice(2);
  if (!target) throw new Error('usage: node build_deck.js <out.pptx> [--single]');
  const [I, F] = [await renderIcons(), await renderFigures()];
  if (mode === '--single') {
    fs.mkdirSync(target, { recursive: true });
    for (let i = 0; i < ORDER.length; i++) {
      await writeDeck(path.join(target, `slide-${String(i + 1).padStart(2, '0')}-${ORDER[i]}.pptx`), [ORDER[i]], I, F);
    }
    console.log(`wrote ${ORDER.length} single-slide files to ${target}`);
  } else {
    await writeDeck(target, ORDER, I, F);
    console.log(`wrote ${target} (${ORDER.length} slides)`);
  }
})().catch((e) => { console.error(e.message || e); process.exit(1); });
