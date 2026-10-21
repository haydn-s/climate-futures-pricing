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
  strata: 'corn_yield_strata_influence.svg',
  intervals: 'corn_yield_intervals.svg',
  decisions: 'corn_yield_decisions.svg',
  efficiency: 'corn_price_information.svg',
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
      text(slide, value, { x, y, w, h: 0.66, fontFace: HEAD, fontSize: size, bold: true, color, align: 'center', valign: 'middle' });
      text(slide, caption, { x, y: y + 0.68, w, h: 0.58, fontSize: 12, color: C.muted, align: 'center', valign: 'top' });
    },
    // Fit a figure inside a box, preserving its aspect ratio.
    figure(slide, fig, box) {
      const h = Math.min(box.h, box.w / fig.ratio);
      const w = h * fig.ratio;
      slide.addImage({ data: fig.data, x: box.x + (box.w - w) / 2, y: box.y + (box.h - h) / 2, w, h });
    },
  };
}

// ------------------------------------------------------------------ notecards
// One card per slide: prompts, not a script, with a time budget for a
// 12-minute slot and the cumulative mark to leave each slide by.
const NOTECARDS = {
  title: `0:20  ·  leave by 0:20

• Haydn Stucker · AIPI 590 final
• One sentence: free weather data measures a corn supply shock, and the market has already priced it
• Road map: what worked, what failed, and why the two sit together`,

  arc: `1:10  ·  leave by 1:30

• Two tests from the proposal: MEASURE the physical shock, PREDICT tradeable returns
• Measurement worked; prediction did not — and that is not a contradiction
• Since the midterm the question changed from "did it work" to "where does the result live"
• Everything that follows concentrates on one season: 2012`,

  measurement: `1:30  ·  leave by 3:00

• County heat and drought beat a training-only trend forecast on held-out harvests
• Forward validation: train only on earlier years, refit the trend inside every fold
• ${pct(D.supply_risk.r2_all_years)} of trend error removed across ${D.supply_risk.test_years} harvests, ${pct(D.supply_risk.r2_excluding_2012)} with 2012 refitted out
• Both positive — that is the defensible claim, and it is narrower than it sounds`,

  concentration: `1:40  ·  leave by 4:40

• Withhold any single harvest and the score stays between ${sign(D.supply_risk.influence_low)} and ${sign(D.supply_risk.influence_high)}
• No single removal flips the sign, so the result is not one season's artifact
• But the skill is concentrated: ${sign(D.supply_risk.severity_high_r2)} in the most extreme third of seasons
• In the calmest third without 2012 it is ${sign(D.supply_risk.severity_low_r2_ex2012)} — worse than doing nothing
• So: a damage-function model. It works when the crop is failing.`,

  uncertainty: `1:40  ·  leave by 6:20

• A point forecast is not a risk estimate — a buyer needs the spread
• Jackknife+ intervals, calibrated only on earlier harvests, coverage measured not assumed
• ${D.uncertainty.covered}/${D.uncertainty.harvests} covered at a nominal ${pct(D.uncertainty.nominal_level)} — but the guarantee is only ${pct(D.uncertainty.guaranteed_level)}
• ${D.uncertainty.tightest_year} clears the bound by ${num(D.uncertainty.tightest_margin_t_per_ha, 3)} t/ha. Every narrower construction misses it
• The price of that coverage: ±${num(D.uncertainty.half_width_pp, 1)}% of trend yield`,

  efficiency: `2:00  ·  leave by 8:20

• Why doesn't it trade? Three reasons, separated — not one excuse
• Race run on the HARVEST, not returns: who knew what, the market or the model?
• December contract explains ${num(D.efficiency.price_r2, 3)}, the nowcast ${num(D.efficiency.nowcast_r2, 3)}, together only ${num(D.efficiency.joint_r2, 3)}
• The nowcast adds ${sign(D.efficiency.incremental_r2)} on top of the price, and ${sign(D.efficiency.incremental_r2_excluding_2012)} without 2012
• Second: break-even R² is ${D.efficiency.detection_ratio.toFixed(0)}× below what ${D.efficiency.observations_available} returns can detect
• Third — and this is the honest part — the backtests lost money, which an invisible edge would not do`,

  provisioning: `1:50  ·  leave by 10:10

• Who can use this? Not a speculator — a buyer who already holds the exposure
• Price-free by construction: no futures position, loss depends on the harvest alone
• Saves ${pct(D.provisioning.saving_all_years)} of provisioning cost against a no-weather baseline
• Control: the model is the NARROWER distribution (${num(D.provisioning.spread_ratio)}×), so it is not just over-provisioning
• Without 2012: ${pct(D.provisioning.saving_excluding_2012)}, but the bootstrap straddles zero — insurance, not a planning tool`,

  nulls: `1:10  ·  leave by 11:20

• Every promoted candidate was frozen before its final test, and every one failed
• GEFS sealed holdout: ${pct(D.trading_nulls.gefs_holdout_net, 1)} over ${D.trading_nulls.gefs_holdout_trades} trades, Sharpe ${num(D.trading_nulls.gefs_holdout_sharpe)}
• USDA Crop Progress: ${pct(D.trading_nulls.usda_net, 1)}, positive in ${D.trading_nulls.usda_positive_years} of ${D.trading_nulls.usda_years} years
• Nothing was retuned after a gate failed. The 2026 holdout stays sealed.
• This is the result, not a setback`,

  close: `0:40  ·  leave by 12:00

• Free climate data measures a corn supply shock — in the seasons that matter
• The market has already priced it by the time the model can compute it
• The estimate is insurance for someone holding the risk, not an edge
• Everything is reproducible: locked protocols, committed outputs, sealed holdouts
• Questions`,
};

// ------------------------------------------------------------------ slides
const SLIDE_FNS = {
  title: (pres, K, I) => {
    const s = pres.addSlide();
    s.background = { color: C.dark };
    K.text(s, 'DUKE AIPI 590 · ALTERNATIVE DATA · FINAL', { x: M, y: 1.5, w: 9, h: 0.35, fontSize: 13, bold: true, color: C.gold, charSpacing: 2, valign: 'middle' });
    K.text(s, 'Climate Impacts on\nFutures Pricing', { x: M, y: 2.0, w: 9, h: 1.9, fontFace: HEAD, fontSize: 48, bold: true, color: C.white, lineSpacingMultiple: 1.05 });
    K.text(s, 'What free weather data can measure, what the market already knows,\nand who can still use the difference', { x: M, y: 4.0, w: 9.2, h: 0.9, fontSize: 17, color: C.mist, lineSpacingMultiple: 1.3 });
    s.addImage({ data: I.cornGold, x: 11.0, y: 2.1, w: 1.5, h: 1.5 });
    K.text(s, 'Haydn Stucker · November 2026', { x: M, y: 5.55, w: 9, h: 0.4, fontSize: 15, color: C.mist, valign: 'middle' });
    return s;
  },

  arc: (pres, K, I) => {
    const s = pres.addSlide();
    K.header(s, 'One project, two tests, one answer',
      'The measurement worked and the trading signal failed — these are not in tension');
    [
      [I.rulerWhite, 'MEASURE', 'Can public climate data identify real crop supply shocks?', 'Yes — in the seasons that matter', C.green],
      [I.chartWhite, 'PREDICT', 'Can those signals predict tradeable commodity returns?', 'No — and now we know why', C.red],
    ].forEach(([icon, head, question, answer, color], i) => {
      const x = M + i * (CW / 2 + 0.2);
      const w = CW / 2 - 0.2;
      K.card(s, x, 1.85, w, 2.5);
      K.iconCircle(s, icon, x + 0.45, 2.2, 0.8, color);
      K.text(s, head, { x: x + 1.5, y: 2.3, w: w - 1.9, h: 0.35, fontSize: 14, bold: true, color, charSpacing: 1.5 });
      K.text(s, question, { x: x + 0.45, y: 3.2, w: w - 0.9, h: 0.5, fontSize: 15, color: C.ink });
      K.text(s, answer, { x: x + 0.45, y: 3.72, w: w - 0.9, h: 0.4, fontSize: 15, bold: true, color });
    });
    K.card(s, M, 4.65, CW, 1.55, C.greenSoft);
    K.text(s, 'THE THROUGH-LINE', { x: M + 0.45, y: 4.9, w: 4, h: 0.3, fontSize: 12, bold: true, color: C.goldText, charSpacing: 1.5 });
    K.text(s, `Every strand of this project concentrates on one season. Remove ${D.supply_risk.most_load_bearing_year} and the measurement weakens, the interval tightens, the decision value stops being detectable, and the market’s informational advantage disappears along with it.`,
      { x: M + 0.45, y: 5.25, w: CW - 0.9, h: 0.8, fontSize: 15, color: C.ink, lineSpacingMultiple: 1.25 });
    K.footer(s, 'Both tests use the same point-in-time archive and the same publication-aware feature construction.');
    return s;
  },

  measurement: (pres, K, I) => {
    const s = pres.addSlide();
    K.header(s, 'What worked: measuring corn supply risk',
      'County weather and drought beat a training-only trend forecast on held-out harvests');
    const d = D.supply_risk;
    [
      [sign(d.r2_all_years), `R² vs trend across ${d.test_years} forward-validated harvests`, C.green],
      [sign(d.r2_excluding_2012), 'the same, with 2012 removed from training and refitted', C.green],
      [num(d.mae_all_years), `mean absolute error, t/ha, against the trend’s ${num(d.trend_mae_all_years)}`, C.ink],
    ].forEach(([value, caption, color], i) => {
      const w = (CW - 0.8) / 3;
      const x = M + i * (w + 0.4);
      K.card(s, x, 1.95, w, 1.9);
      K.stat(s, x, 2.15, w, value, caption, color, 42);
    });
    K.card(s, M, 4.15, CW, 1.05, C.greenSoft);
    K.text(s, 'Both samples positive. Every fold trains only on earlier harvests and refits the yield trend inside itself, so no held-out year helped define what counts as normal for itself.',
      { x: M + 0.45, y: 4.4, w: CW - 0.9, h: 0.6, fontSize: 15, color: C.ink, lineSpacingMultiple: 1.2 });
    K.text(s, 'This is a post-harvest attribution result. County weather is not published in time to inform an in-season decision — the availability audit found current-season county data in only 3 of 26 years.',
      { x: M, y: 5.45, w: CW, h: 0.8, fontSize: 14, color: C.muted, lineSpacingMultiple: 1.2 });
    K.footer(s, 'Metric: R² against each fold’s own trend forecast. Source: corn_yield_forward_validation_results.csv.');
    return s;
  },

  concentration: (pres, K, I, F) => {
    const s = pres.addSlide();
    K.header(s, 'Where the result actually lives',
      'Not one season — but concentrated in the seasons that went wrong');
    K.figure(s, F.strata, { x: M, y: 1.75, w: CW, h: 2.85 });
    const d = D.supply_risk;
    [
      [`${sign(d.influence_low)} to ${sign(d.influence_high)}`, 'range when any single harvest is withheld from the score', C.green],
      [sign(d.severity_high_r2), 'in the third of seasons furthest from trend', C.green],
      [sign(d.severity_low_r2_ex2012), 'in the calmest third, with 2012 refitted out', C.red],
    ].forEach(([value, caption, color], i) => {
      const w = (CW - 0.8) / 3;
      K.stat(s, M + i * (w + 0.4), 4.75, w, value, caption, color, 26);
    });
    K.footer(s, 'Withholding a harvest from a score is not the exclude-2012 sample, which also drops it from training and refits every fold.');
    return s;
  },

  uncertainty: (pres, K, I, F) => {
    const s = pres.addSlide();
    K.header(s, 'How precise is the estimate?',
      'Calibrated intervals, with coverage measured rather than assumed');
    K.figure(s, F.intervals, { x: M, y: 1.75, w: CW, h: 2.85 });
    const d = D.uncertainty;
    [
      [`${d.covered}/${d.harvests}`, `covered at a nominal ${pct(d.nominal_level)}; the guarantee is only ${pct(d.guaranteed_level)}`, C.green],
      [`±${num(d.half_width_pp, 1)}%`, 'of trend yield — the honest resolution of the estimate', C.ink],
      [`${num(d.tightest_margin_t_per_ha, 3)} t/ha`, `the margin by which ${d.tightest_year} stays inside its interval`, C.red],
    ].forEach(([value, caption, color], i) => {
      const w = (CW - 0.8) / 3;
      K.stat(s, M + i * (w + 0.4), 4.75, w, value, caption, color, 26);
    });
    K.footer(s, 'Jackknife+ on chronological folds; every narrower construction tested leaves 2012 outside.');
    return s;
  },

  efficiency: (pres, K, I, F) => {
    const s = pres.addSlide();
    K.header(s, 'Why it does not become a trade',
      'Three separate reasons, not one excuse');
    K.figure(s, F.efficiency, { x: M, y: 1.7, w: CW, h: 2.6 });
    const d = D.efficiency;
    [
      [I.scaleWhite, 'ALREADY PRICED', `The December contract explains ${num(d.price_r2, 3)} of the harvest on its own. The nowcast adds ${sign(d.incremental_r2)}, and ${sign(d.incremental_r2_excluding_2012)} without 2012.`, C.green],
      [I.searchWhite, 'BELOW RESOLUTION', `Break-even is an R² of ${d.break_even_r2.toFixed(5)}; ${d.observations_available} independent returns can only detect ${d.detectable_r2.toFixed(5)}. A factor of ${d.detection_ratio.toFixed(0)}.`, C.gold],
      [I.warnWhite, 'AND IT LOST MONEY', 'An edge too small to detect scatters results near zero. It does not produce a 52% loss. That is evidence in its own right.', C.red],
    ].forEach(([icon, head, body, color], i) => {
      const w = (CW - 0.7) / 3;
      const x = M + i * (w + 0.35);
      K.iconCircle(s, icon, x, 4.45, 0.62, color);
      K.text(s, head, { x: x + 0.78, y: 4.55, w: w - 0.78, h: 0.3, fontSize: 12, bold: true, color, charSpacing: 1 });
      K.text(s, body, { x, y: 5.25, w, h: 1.3, fontSize: 13, color: C.ink, lineSpacingMultiple: 1.2 });
    });
    K.footer(s, 'The race is run on the harvest, not on returns: it asks who knew what, rather than whether the signal predicts the price.');
    return s;
  },

  provisioning: (pres, K, I, F) => {
    const s = pres.addSlide();
    K.header(s, 'Who can still use it',
      'A buyer holding the exposure, not a speculator — and the test is price-free');
    K.figure(s, F.decisions, { x: M, y: 1.75, w: CW, h: 2.8 });
    const d = D.provisioning;
    [
      [pct(d.saving_all_years), `provisioning cost saved against a no-weather baseline, at a ${d.penalty_ratio}:1 penalty`, C.green],
      [`${num(d.spread_ratio)}×`, 'the model is the narrower distribution — so this is not over-provisioning', C.ink],
      [pct(d.saving_excluding_2012), `without 2012 — but the bootstrap straddles zero (P = ${pct(d.probability_positive_excluding_2012)})`, C.gold],
    ].forEach(([value, caption, color], i) => {
      const w = (CW - 0.8) / 3;
      K.stat(s, M + i * (w + 0.4), 4.7, w, value, caption, color, 26);
    });
    K.footer(s, 'No futures price enters the decision, so it cannot restate the rejected trading signal as a profit.');
    return s;
  },

  nulls: (pres, K, I) => {
    const s = pres.addSlide();
    K.header(s, 'What failed, and how we know',
      'Every candidate was frozen before its final test — and none was retuned afterwards');
    const d = D.trading_nulls;
    const rows = [
      ['GEFS forecast revisions', `${pct(d.gefs_holdout_net, 1)} net`, `Sharpe ${num(d.gefs_holdout_sharpe)}`, `${d.gefs_holdout_trades} sealed-holdout trades`],
      ['USDA Crop Progress', `${pct(d.usda_net, 1)} net`, `Sharpe ${num(d.usda_sharpe)}`, `positive in ${d.usda_positive_years} of ${d.usda_years} years`],
    ];
    rows.forEach(([name, net, sharpe, detail], i) => {
      const y = 1.95 + i * 1.15;
      K.card(s, M, y, CW, 0.95, C.redSoft);
      K.text(s, name, { x: M + 0.4, y, w: 4.2, h: 0.95, fontSize: 16, bold: true, color: C.ink, valign: 'middle' });
      K.text(s, net, { x: M + 4.6, y, w: 2.0, h: 0.95, fontFace: HEAD, fontSize: 22, bold: true, color: C.red, align: 'center', valign: 'middle' });
      K.text(s, sharpe, { x: M + 6.7, y, w: 2.0, h: 0.95, fontSize: 15, color: C.muted, align: 'center', valign: 'middle' });
      K.text(s, detail, { x: M + 8.8, y, w: CW - 9.2, h: 0.95, fontSize: 14, color: C.muted, valign: 'middle' });
    });
    K.card(s, M, 4.45, CW, 1.75, C.panel);
    K.text(s, 'THE DISCIPLINE IS THE RESULT', { x: M + 0.45, y: 4.7, w: 6, h: 0.3, fontSize: 12, bold: true, color: C.goldText, charSpacing: 1.5 });
    [
      'Candidate identity, costs and gate checks were committed before each reveal.',
      'Seven predeclared checks failed on the USDA model, so its 2026 holdout was never opened.',
      'No specification was retuned after a gate failure — the avenue was closed instead.',
    ].forEach((line, i) => {
      s.addImage({ data: I.checkGreen, x: M + 0.5, y: 5.12 + i * 0.36, w: 0.16, h: 0.16 });
      K.text(s, line, { x: M + 0.82, y: 5.06 + i * 0.36, w: CW - 1.4, h: 0.32, fontSize: 13, color: C.ink, valign: 'middle' });
    });
    K.footer(s, 'Sources: gefs_corn_holdout_results.csv, nass_corn_progress_validation_results.csv.');
    return s;
  },

  close: (pres, K, I) => {
    const s = pres.addSlide();
    s.background = { color: C.dark };
    K.text(s, 'THE ANSWER', { x: M, y: 1.3, w: 6, h: 0.35, fontSize: 13, bold: true, color: C.gold, charSpacing: 2, valign: 'middle' });
    K.text(s, 'Free weather data measures a real\nsupply shock. The market already\nknows about it.', { x: M, y: 1.75, w: 7.6, h: 2.5, fontFace: HEAD, fontSize: 34, bold: true, color: C.white, lineSpacingMultiple: 1.15 });
    [
      ['Measurement', 'Validated forward, concentrated in extreme seasons'],
      ['Efficiency', 'Priced in by the time the model can compute it'],
      ['Use', 'Insurance for a buyer holding the risk — not an edge'],
    ].forEach(([head, sub], i) => {
      const y = 1.85 + i * 1.25;
      K.iconCircle(s, [I.rulerWhite, I.scaleWhite, I.shieldWhite][i], 8.7, y, 0.78, C.green);
      K.text(s, [
        { text: head, options: { bold: true, fontSize: 17, color: C.white, breakLine: true } },
        { text: sub, options: { fontSize: 13, color: C.mist } },
      ], { x: 9.68, y, w: W - M - 9.68, h: 0.9, valign: 'middle' });
    });
    K.text(s, 'Questions?', { x: M, y: 4.9, w: 6, h: 0.7, fontFace: HEAD, fontSize: 30, bold: true, color: C.gold, valign: 'middle' });
    s.addImage({ data: I.githubMist, x: M, y: 5.95, w: 0.3, h: 0.3 });
    K.text(s, 'github.com/haydn-s/climate-futures-pricing', { x: M + 0.42, y: 5.93, w: 6.5, h: 0.35, fontSize: 14, color: C.mist, valign: 'middle' });
    return s;
  },
};

// Deck order, as presented. Reorder these keys to reorder the deck.
const ORDER = ['title', 'arc', 'measurement', 'concentration', 'uncertainty',
  'efficiency', 'provisioning', 'nulls', 'close'];

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
    s.addNotes(NOTECARDS[key]);
  });
  fs.mkdirSync(path.dirname(file), { recursive: true });
  await pres.writeFile({ fileName: file });
}

(async () => {
  const missing = ORDER.filter((key) => !SLIDE_FNS[key] || !NOTECARDS[key]);
  if (missing.length) throw new Error(`ORDER names a slide or notecard that does not exist: ${missing}`);
  if (new Set(ORDER).size !== ORDER.length) throw new Error('duplicate key in ORDER');
  if (ORDER.length !== Object.keys(SLIDE_FNS).length) throw new Error('every slide must appear in ORDER exactly once');
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
