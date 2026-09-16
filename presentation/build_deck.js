// Proposal deck for Climate Futures Pricing.
//   node build_deck.js out.pptx            full deck
//   node build_deck.js outdir --single     one file per slide, for previews
const fs = require('fs');
const path = require('path');
const pptxgen = require('pptxgenjs');
const JSZip = require('jszip');
const React = require('react');
const ReactDOMServer = require('react-dom/server');
const sharp = require('sharp');
const fa = require('react-icons/fa');
const gi = require('react-icons/gi');

const D = JSON.parse(fs.readFileSync(path.join(__dirname, 'chart_data.json'), 'utf8'));

const C = {
  dark: '16302A', green: '2E6B4F', greenSoft: 'DCEBE2', mist: 'BFD3C8',
  gold: 'E1A624', goldText: '9A6D0B', red: 'C2502D', redSoft: 'F9E7E2',
  ink: '1D2723', muted: '5E6A64', line: 'D6DDD8', grid: 'E3E8E5', panel: 'F2F5F3', white: 'FFFFFF', gray: 'AEB7B2',
};
const HEAD = 'Georgia';
const BODY = 'Arial';
const W = 13.333;
const M = 0.6;
const CW = W - 2 * M;
const MINUS = '−';
const fmtR = (r) => (r < 0 ? MINUS : '+') + Math.abs(r).toFixed(2);
const pct = (v) => (v < 0 ? MINUS : '+') + Math.abs(Math.round(v)) + '%';

// ------------------------------------------------------------------ numbers used in text
const yieldGap = (year) => D.scatter.y[D.scatter.year.indexOf(year)];
const m = D.measurement;
const a = D.annual;
const t12 = D.y2012;
const leadDays = (Date.parse(t12.first_half_iowa_map) - Date.parse(t12.half_done)) / 86400000;
if (Math.floor(leadDays / 7) !== 2) throw new Error(`expected a two-week lead, got ${leadDays} days`);
const WORST = [2012, 2002, 2011].map((y) => `${y} (${pct(yieldGap(y))})`).join(', ');

// ------------------------------------------------------------------ icons
const ICONS = {
  cornDark: [gi.GiCorn, C.dark], heatWhite: [fa.FaThermometerHalf, C.white], sunWhite: [fa.FaSun, C.white],
  chartWhite: [fa.FaChartLine, C.white], mapWhite: [fa.FaMapMarkedAlt, C.white], hourglassWhite: [fa.FaHourglassHalf, C.white],
  coffeeGreen: [gi.GiCoffeeBeans, C.green], buildingGreen: [fa.FaBuilding, C.green],
  industryWhite: [fa.FaIndustry, C.white], truckWhite: [fa.FaTruck, C.white], tractorWhite: [fa.FaTractor, C.white],
  shieldWhite: [fa.FaShieldAlt, C.white], landmarkWhite: [fa.FaLandmark, C.white],
  mapGreen: [fa.FaMapMarkedAlt, C.green], heatGreen: [fa.FaThermometerHalf, C.green], calcGreen: [fa.FaCalculator, C.green],
  chartGreen: [fa.FaChartLine, C.green], globeGreen: [fa.FaGlobeAmericas, C.green],
  checkWhite: [fa.FaCheck, C.white], checkGreen: [fa.FaCheck, C.green], warnRed: [fa.FaExclamationTriangle, C.red],
  arrowGray: [fa.FaArrowRight, C.gray], rulerWhite: [fa.FaRulerCombined, C.white], stopwatchWhite: [fa.FaStopwatch, C.white],
  bulbWhite: [fa.FaLightbulb, C.white], githubMist: [fa.FaGithub, C.mist],
  dbGreen: [fa.FaDatabase, C.green], peopleWhite: [fa.FaUsers, C.white], bulbGreen: [fa.FaLightbulb, C.green],
};

async function renderIcons() {
  const out = {};
  for (const [name, [Icon, color]] of Object.entries(ICONS)) {
    const svg = ReactDOMServer.renderToStaticMarkup(React.createElement(Icon, { size: '512' })).replace(/currentColor/g, '#' + color);
    const png = await sharp(Buffer.from(svg)).resize(512, 512).png().toBuffer();
    out[name] = 'image/png;base64,' + png.toString('base64');
  }
  return out;
}

// ------------------------------------------------------------------ layout kit
function kit(pres) {
  const text = (slide, str, o) =>
    slide.addText(str, Object.assign({ fontFace: BODY, fontSize: 14, color: C.ink, margin: 0, valign: 'top', isTextBox: true }, o));
  return {
    text,
    header(slide, title, subtitle) {
      text(slide, title, { x: M, y: 0.42, w: CW, h: 0.72, fontFace: HEAD, fontSize: 36, bold: true, valign: 'middle' });
      text(slide, subtitle, { x: M, y: 1.14, w: CW, h: 0.44, fontSize: 18, color: C.muted, valign: 'middle' });
    },
    footer(slide, str) {
      text(slide, str, { x: M, y: 6.9, w: 11.4, h: 0.3, fontSize: 10, color: C.muted, valign: 'middle' });
    },
    number(slide, dark = false) {
      slide.slideNumber = { x: 12.23, y: 6.9, w: 0.5, h: 0.3, fontFace: BODY, fontSize: 10, color: dark ? C.mist : C.muted, align: 'right' };
    },
    circle(slide, x, y, d, color) {
      slide.addShape(pres.shapes.OVAL, { x, y, w: d, h: d, fill: { color }, line: { color, width: 0.75 } });
    },
    iconCircle(slide, icon, x, y, d, fill, pad = 0.24) {
      this.circle(slide, x, y, d, fill);
      const p = d * pad;
      slide.addImage({ data: icon, x: x + p, y: y + p, w: d - 2 * p, h: d - 2 * p });
    },
    numCircle(slide, n, x, y, d, fill, color, size) {
      this.circle(slide, x, y, d, fill);
      text(slide, String(n), { x, y, w: d, h: d, fontFace: BODY, fontSize: size, bold: true, color, align: 'center', valign: 'middle' });
    },
    card(slide, x, y, w, h, fill = C.panel) {
      slide.addShape(pres.shapes.ROUNDED_RECTANGLE, { x, y, w, h, fill: { color: fill }, line: { color: fill, width: 0.75 }, rectRadius: 0.08 });
    },
  };
}

const axisText = () => ({
  catAxisLabelColor: C.muted, catAxisLabelFontFace: BODY, catAxisLabelFontSize: 11,
  valAxisLabelColor: C.muted, valAxisLabelFontFace: BODY, valAxisLabelFontSize: 11,
});

// ------------------------------------------------------------------ notecards
// One card per slide: prompts, not a script. Each shows its own length and the
// cumulative mark to leave that slide by, for a 10-minute slot.
const NOTECARDS = {
  title: `0:15  ·  leave by 0:15

• Haydn Stucker · AIPI 590 project proposal
• One question: does the futures market price weather before the data does?
• Road map: the problem, the data and who needs it, then the plan`,

  problem: `1:00  ·  leave by 1:15

• Heat and drought hit crops, low rivers stall barges, storms close ports
• Flows through to futures, food costs, groceries
• Weather data is free, global, decades deep — looks like ideal alt data
• Hard part 1: supply chains are opaque → map exposure from where crops grow
• Hard part 2: observations publish late; markets trade forecasts continuously
• So: can free climate data measure supply shocks, and tell the market anything new?`,

  questions: `0:45  ·  leave by 2:00

• Measure — can the data capture real supply shocks? Validate on yields first
• Timing — do futures respond before or after publication?
• Value — does a risk score beat what prices already contain? Out of sample
• Stretch: are cocoa and coffee slower? does it reach agribusiness stocks?`,

  stakeholders: `0:45  ·  leave by 2:45

• Traders and funds — price risk, signals
• Food and beverage makers — procurement, hedging
• Grain handlers, barge and rail, ports — physical disruption
• Farmers and co-ops — hedging, marketing
• Insurers and lenders — crop insurance, ag credit
• Policymakers and consumers — food security, prices
• Land this: a validated score helps everyone who does not trade`,

  datasets: `1:05  ·  leave by 3:50

• All free and public, 15+ years of history
• NASA POWER — daily temperature and rain, any location
• Drought Monitor — weekly by county and state, published Thursdays → lets me test timing
• Yahoo Finance — corn, soy, wheat, coffee, cocoa, OJ; Teucrium CORN avoids roll jumps
• FAO yields via Our World in Data — the answer key
• Next: NOAA Storm Events, and USDA county production (free key) for weights
• Everything but USDA already pulled and checked`,

  measurement: `0:50  ·  leave by 4:40

• Feasibility check before committing to the project
• 5 corn-belt points, equal weights; index = July heat + summer rain + peak drought
• r = ${fmtR(m.r_all)} against yield vs trend
• Without 2012: ${fmtR(m.r_ex2012)}, still significant. July heat alone ${fmtR(m.r_heat)}
• Point: the measurement works even this crude`,

  case2012: `0:55  ·  leave by 5:35

• Worst corn-belt drought in decades
• Green = corn futures; red = share of Iowa in severe drought
• July 3: corn +${Math.round(t12.pct_jul3)}%, but only ${Math.round(t12.iowa_jul3)}% of Iowa in severe drought
• Half the rally done by July 2 — two weeks before the map showed half the state
• Maps publish two days after their date
• Drought was real: yields ${pct(yieldGap(2012))} vs trend. The market did not wait for the data`,

  timing: `1:00  ·  leave by 6:35

• Bars = correlation of weekly corn returns with drought change, by timing
• Green (1–3 weeks before publication) strongest; publication week ≈ 0
• Drought builds slowly → also estimated every lead and lag jointly
• Still leads by 1–2 weeks, nothing after publication significant, holds without 2012
• Annual story is almost all 2012: ${fmtR(a.r_corn)} → ${fmtR(a.r_corn_ex2012)}
• So the market may already know — the project measures how early`,

  value: `0:45  ·  leave by 7:20

• BCG 10-20-70 — 10% algorithms, 20% data and tech, 70% people and process
• My algorithms are deliberately simple
• Most of the semester is the 20%: exposure, pipeline, publication dates
• The 70% is the stakeholder slide: whose decision changes, and when
• If the market already prices weather, that was only the 10%
• Caveat: enterprise rule, I am one person — it guides effort, not a change programme`,

  approach: `1:05  ·  leave by 8:25

• 1 Map exposure — USDA county production weights replace equal weights
• 2 Measure stress — heat above 29 °C, rain, drought in each crop's window
• 3 Build the risk score — validate on yields before touching prices
• 4 Link to prices — lead-lag and event studies; control seasonality, USDA reports, rolls
• 5 Extend — forecasts, cocoa and coffee, agribusiness stocks
• Phases follow CRISP-ML(Q): each has a validation gate
• Guardrails: publication-date data, out of sample, every result re-run without 2012`,

  plan: `0:45  ·  leave by 9:10

• Data first — automated, re-runnable pipeline over the next few weeks
• October — exposure weights and stress measures, then the score and yield validation
• November — the price tests
• Extensions alongside, as time allows
• Early December — write-up and final presentation`,

  risks: `0:35  ·  leave by 9:45

• Market already knows → how early it knows is the finding; forecasts, cocoa
• One extreme year → every result with and without 2012; more crops and regions
• Observed weather lags forecasts → track publication lags; forecasts as an extension
• Contract rolls → cross-check the CORN fund, control roll dates
• Five points too crude → USDA county weights`,

  close: `0:15  ·  leave by 10:00

• Measure weather stress, validated on yields
• Time market moves against publication dates
• Test whether a risk score adds anything new
• Thanks — happy to take questions`,
};

// ------------------------------------------------------------------ slides
const SLIDE_FNS = {
  title: (pres, K, I) => {
    const s = pres.addSlide();
    s.background = { color: C.dark };
    K.text(s, 'DUKE AIPI 590  ·  ALTERNATIVE DATA  ·  PROJECT PROPOSAL', { x: M, y: 1.45, w: 7.9, h: 0.35, fontSize: 13, bold: true, color: C.gold, charSpacing: 2, valign: 'middle' });
    K.text(s, 'Climate Futures Pricing', { x: M, y: 1.95, w: 7.9, h: 1.95, fontFace: HEAD, fontSize: 54, bold: true, color: C.white });
    K.text(s, 'Does the futures market price weather before the data does?', { x: M, y: 4.0, w: 7.4, h: 1.0, fontSize: 24, color: C.mist });
    K.text(s, 'Haydn Stucker  ·  September 2026', { x: M, y: 5.55, w: 7.4, h: 0.4, fontSize: 15, color: C.white, valign: 'middle' });
    K.iconCircle(s, I.cornDark, 9.35, 1.55, 3.0, C.gold, 0.2);
    K.iconCircle(s, I.heatWhite, 8.7, 4.2, 1.3, C.red);
    K.iconCircle(s, I.chartWhite, 11.35, 4.45, 1.3, C.green);
    return s;
  },

  case2012: (pres, K) => {
    const s = pres.addSlide();
    K.header(s, '2012: the market moved first', 'Corn futures priced the drought before the Drought Monitor showed it');
    const wk = t12.weeks_map;
    const labels = wk.map((w) => w.label);
    s.addChart(pres.charts.LINE, [
      { name: 'Corn futures, % change since June 1', labels, values: wk.map((w) => w.corn_pct) },
      { name: 'Iowa in severe drought, % of state', labels, values: wk.map((w) => w.iowa_d2) },
    ], Object.assign(axisText(), {
      x: M, y: 1.8, w: 8.0, h: 4.95,
      chartColors: [C.green, C.red], lineSize: 3, lineDataSymbol: 'none',
      showLegend: true, legendPos: 't', legendFontFace: BODY, legendFontSize: 12, legendColor: C.ink,
      catAxisLabelFrequency: 2, catAxisLabelRotate: 0, catAxisLineShow: true, catAxisLineColor: C.line, valAxisLineShow: false,
      valAxisMinVal: 0, valAxisMaxVal: 100, valAxisMajorUnit: 25, valAxisLabelFormatCode: '0\\%',
      valGridLine: { color: C.grid, size: 0.75 }, catGridLine: { style: 'none' },
    }));
    const stat = (big, color, label, y) => {
      K.text(s, big, { x: 9.0, y, w: 3.73, h: 0.8, fontFace: BODY, fontSize: 44, bold: true, color, valign: 'middle' });
      K.text(s, label, { x: 9.0, y: y + 0.82, w: 3.73, h: 0.75, fontSize: 14 });
    };
    stat(pct(t12.pct_jul3), C.green, `Corn futures by July 3, when ${Math.round(t12.iowa_jul3)}% of Iowa was in severe drought`, 1.85);
    stat('2 weeks', C.red, 'Half the rally was done before half of Iowa was in severe drought', 3.5);
    stat(pct(yieldGap(2012)), C.ink, 'US corn yield vs trend that year, the largest shortfall since 2000', 5.15);
    K.footer(s, 'Weekly, June–September 2012. Maps are published two days after the dates shown. Sources: Yahoo Finance (ZC=F), US Drought Monitor.');
    return s;
  },

  problem: (pres, K, I) => {
    const s = pres.addSlide();
    K.header(s, 'The problem', 'Weather breaks supply chains, but weather data is hard to turn into a signal');
    const chain = [
      [I.sunWhite, C.red, 'Heat and drought', 'Crops fail, rivers run low', 2.6],
      [I.cornDark, C.gold, 'Supply shortfalls', 'Lost harvests, idle barges', 2.6],
      [I.chartWhite, C.green, 'Prices move', 'Futures, food costs, groceries', 2.78],
    ];
    chain.forEach(([icon, fill, head, sub, lw], i) => {
      const x = M + i * 4.15;
      K.iconCircle(s, icon, x, 1.95, 0.9, fill, i === 1 ? 0.16 : 0.24);
      K.text(s, [
        { text: head, options: { bold: true, fontSize: 17, breakLine: true } },
        { text: sub, options: { fontSize: 13, color: C.muted } },
      ], { x: x + 1.05, y: 1.95, w: lw, h: 0.9, valign: 'middle' });
      if (i < 2) s.addShape(pres.shapes.LINE, { x: x + 3.7, y: 2.4, w: 0.35, h: 0, line: { color: C.gray, width: 2, endArrowType: 'triangle' } });
    });
    const cards = [
      [I.mapWhite, 'Supply chains are opaque', 'Exposure is geographic, but companies rarely disclose where supply comes from, so it must be mapped from where crops are grown.'],
      [I.hourglassWhite, 'Weather data arrives late', 'Observations are published days to weeks after the fact, while futures react to forecasts continuously.'],
    ];
    cards.forEach(([icon, head, body], i) => {
      const x = M + i * (5.92 + 0.29);
      const y = 3.2;
      K.card(s, x, y, 5.92, 2.0);
      K.iconCircle(s, icon, x + 0.3, y + 0.32, 0.75, C.dark);
      K.text(s, head, { x: x + 1.3, y: y + 0.3, w: 4.35, h: 0.5, fontSize: 18, bold: true, valign: 'middle' });
      K.text(s, body, { x: x + 1.3, y: y + 0.82, w: 4.35, h: 1.05, fontSize: 15, color: C.muted });
    });
    K.card(s, M, 5.45, CW, 1.3, C.dark);
    K.text(s, 'PROBLEM STATEMENT', { x: 0.95, y: 5.6, w: 4, h: 0.3, fontSize: 12, bold: true, color: C.gold, charSpacing: 2, valign: 'middle' });
    K.text(s, 'Can free climate data, mapped to where crops grow, measure supply shocks, and does it tell the futures market anything it does not already know?',
      { x: 0.95, y: 5.96, w: 11.45, h: 0.68, fontFace: HEAD, fontSize: 19, color: C.white, valign: 'middle' });
    return s;
  },

  questions: (pres, K, I) => {
    const s = pres.addSlide();
    K.header(s, 'Research questions', 'Three core questions, two stretch goals');
    const qs = [
      ['MEASURE', 'Can free climate data, mapped to where crops grow, measure supply shocks?', 'Validated against actual crop yields'],
      ['TIMING', 'Do futures prices respond to weather stress, and before or after the data is published?', 'Tested against publication dates'],
      ['VALUE', 'Does a climate risk score tell the market anything it does not already know?', 'Out of sample, against price-only baselines'],
    ];
    const cw = (CW - 2 * 0.3) / 3;
    qs.forEach(([tag, q, note], i) => {
      const x = M + i * (cw + 0.3);
      const y = 1.9;
      K.card(s, x, y, cw, 3.25);
      K.numCircle(s, i + 1, x + 0.3, y + 0.3, 0.64, C.dark, C.white, 20);
      K.text(s, tag, { x: x + 1.1, y: y + 0.3, w: cw - 1.4, h: 0.64, fontSize: 14, bold: true, color: C.green, charSpacing: 2, valign: 'middle' });
      K.text(s, q, { x: x + 0.3, y: y + 1.15, w: cw - 0.6, h: 1.4, fontSize: 18 });
      K.text(s, note, { x: x + 0.3, y: y + 2.6, w: cw - 0.6, h: 0.5, fontSize: 13, color: C.muted, italic: true });
    });
    K.text(s, 'EXPLORATORY, AS TIME ALLOWS', { x: M, y: 5.42, w: 6, h: 0.3, fontSize: 12, bold: true, color: C.muted, charSpacing: 2, valign: 'middle' });
    const extra = [
      [I.coffeeGreen, '4', 'Do tree crops like cocoa and coffee price weather more slowly?', M],
      [I.buildingGreen, '5', 'Does the effect reach agribusiness stocks?', M + 6.21],
    ];
    extra.forEach(([icon, n, q, x]) => {
      K.iconCircle(s, icon, x, 5.85, 0.7, C.greenSoft, 0.22);
      K.text(s, [
        { text: n + '   ', options: { bold: true, color: C.green } },
        { text: q },
      ], { x: x + 0.88, y: 5.85, w: 5.0, h: 0.7, fontSize: 16, valign: 'middle' });
    });
    return s;
  },

  stakeholders: (pres, K, I) => {
    const s = pres.addSlide();
    K.header(s, 'Stakeholders', 'Who needs to know when weather becomes a supply shock');
    const people = [
      [I.chartWhite, 'Commodity traders and funds', 'Price risk and trading signals'],
      [I.industryWhite, 'Food and beverage manufacturers', 'Procurement costs and hedging'],
      [I.truckWhite, 'Agribusiness and logistics', 'Grain handlers, barge and rail operators, ports'],
      [I.tractorWhite, 'Farmers and cooperatives', 'Hedging and marketing decisions'],
      [I.shieldWhite, 'Insurers and lenders', 'Crop insurance and agricultural credit risk'],
      [I.landmarkWhite, 'Policymakers and consumers', 'Food security and food prices'],
    ];
    const cw = (CW - 2 * 0.3) / 3;
    people.forEach(([icon, who, why], i) => {
      const x = M + (i % 3) * (cw + 0.3);
      const y = 1.9 + Math.floor(i / 3) * 2.35;
      K.card(s, x, y, cw, 2.05);
      K.iconCircle(s, icon, x + 0.3, y + 0.32, 0.8, C.dark);
      K.text(s, who, { x: x + 1.3, y: y + 0.32, w: cw - 1.55, h: 0.8, fontSize: 17, bold: true, valign: 'middle' });
      K.text(s, why, { x: x + 0.3, y: y + 1.3, w: cw - 0.6, h: 0.6, fontSize: 14, color: C.muted });
    });
    K.text(s, 'Even if traders already price the weather, a validated risk score helps everyone who does not trade.',
      { x: M, y: 6.42, w: 11.4, h: 0.4, fontSize: 15, italic: true, color: C.green, valign: 'middle' });
    return s;
  },

  datasets: (pres, K) => {
    const s = pres.addSlide();
    K.header(s, 'Datasets', 'Free, public data with at least 15 years of history');
    const head = ['Source', 'What it measures', 'History', 'Access', 'Status'].map((h) => ({ text: h, options: { bold: true, color: C.white, fill: { color: C.dark } } }));
    const STATUS = { use: ['In use', C.green], next: ['Next up', C.goldText], key: ['Needs key', C.red] };
    const row = (src, what, hist, access, st, shade) => {
      const cell = (text, extra = {}) => ({ text, options: Object.assign({ fill: { color: shade ? C.panel : C.white } }, extra) });
      return [
        cell(src, { bold: true }), cell(what), cell(hist), cell(access),
        cell([{ text: '● ', options: { color: STATUS[st][1] } }, { text: STATUS[st][0] }]),
      ];
    };
    const rows = [
      head,
      row('NASA POWER', 'Daily temperature and rainfall for any location, about 3 days behind', '1981 → now', 'Free, no key', 'use', false),
      row('US Drought Monitor', 'Weekly share of area in drought by county and state, published Thursdays', '2000 → now', 'Free, no key', 'use', true),
      row('Yahoo Finance', 'Daily futures: corn, soybeans, wheat, coffee, cocoa, orange juice', '2000–01 → now', 'Free, no key', 'use', false),
      row('Teucrium CORN fund', 'Corn price without contract-roll jumps', '2010 → now', 'Free, no key', 'use', true),
      row('FAO crop yields', 'Annual national yields for US corn and West African cocoa, via Our World in Data', '2000 → 2024–25', 'Free, no key', 'use', false),
      row('NOAA Storm Events', 'Severe weather events with property and crop damage', '1950 → now', 'Free, no key', 'next', true),
      row('USDA NASS Quick Stats', 'County crop production, to weight locations by output', 'Decades', 'Free API key', 'key', false),
    ];
    s.addTable(rows, {
      x: M, y: 1.85, w: CW, colW: [2.6, 4.55, 1.75, 1.55, 1.68], rowH: 0.6,
      fontFace: BODY, fontSize: 14, color: C.ink, valign: 'middle',
      border: { type: 'solid', pt: 0.75, color: C.line }, margin: [0.05, 0.1, 0.05, 0.1],
    });
    K.footer(s, 'All sources except USDA NASS were pulled and checked for this proposal; publication lags are recorded so tests use only data available at the time.');
    return s;
  },

  measurement: (pres, K) => {
    const s = pres.addSlide();
    K.header(s, 'Early check: measurement', 'A simple weather index already tracks corn yield shortfalls');
    const sc = D.scatter;
    K.text(s, [
      { text: '●  ', options: { color: C.green } }, { text: 'Each year, 2000–2025      ', options: { color: C.ink } },
      { text: '●  ', options: { color: C.red } }, { text: '2012', options: { color: C.ink } },
    ], { x: M + 0.1, y: 1.78, w: 7.7, h: 0.35, fontSize: 12, valign: 'middle' });
    s.addChart(pres.charts.SCATTER, [
      { name: 'X', values: sc.x },
      { name: 'US corn yield vs trend', values: sc.y },
    ], Object.assign(axisText(), {
      x: M, y: 2.1, w: 7.9, h: 4.65,
      chartColors: [C.green], lineSize: 0, lineDataSymbol: 'circle', lineDataSymbolSize: 11, showLegend: false,
      valAxisMinVal: -25, valAxisMaxVal: 15, valAxisMajorUnit: 5, valAxisLineShow: false, catAxisCrossesAt: -1.5,
      catAxisMinVal: -1.5, catAxisMaxVal: 3.5, catAxisMajorUnit: 1, catAxisLineColor: C.gray, valAxisCrossesAt: -25,
      showValAxisTitle: true, valAxisTitle: 'US corn yield vs trend (%)', valAxisTitleColor: C.muted, valAxisTitleFontFace: BODY, valAxisTitleFontSize: 12,
      showCatAxisTitle: true, catAxisTitle: 'Weather stress index (z-score; hotter and drier to the right)', catAxisTitleColor: C.muted, catAxisTitleFontFace: BODY, catAxisTitleFontSize: 12,
      valGridLine: { color: C.grid, size: 0.75 }, catGridLine: { style: 'none' },
    }));
    const x = 8.95;
    const w = 3.78;
    K.text(s, fmtR(m.r_all), { x, y: 1.85, w, h: 0.85, fontFace: BODY, fontSize: 48, bold: true, color: C.green, valign: 'middle' });
    K.text(s, 'Correlation between the index and yield vs trend, 2000–2025', { x, y: 2.72, w, h: 0.55 });
    K.text(s, fmtR(m.r_ex2012), { x, y: 3.5, w, h: 0.85, fontFace: BODY, fontSize: 48, bold: true, color: C.ink, valign: 'middle' });
    K.text(s, `Still significant without 2012 (p = ${m.p_ex2012.toFixed(2)})`, { x, y: 4.37, w, h: 0.55 });
    K.card(s, x, 5.15, w, 1.55);
    K.text(s, [
      { text: 'July heat alone: ', options: { bold: true } },
      { text: `r = ${fmtR(m.r_heat)}`, options: { breakLine: true } },
      { text: 'Largest shortfalls:', options: { bold: true, breakLine: true } },
      { text: WORST },
    ], { x: x + 0.2, y: 5.3, w: w - 0.4, h: 1.25, fontSize: 13, valign: 'middle', paraSpaceAfter: 4 });
    K.footer(s, 'Index: July heat, June–August rain (inverted) and peak drought at five corn-belt points (z-scores). Yields: FAO via Our World in Data, vs trend.');
    return s;
  },

  timing: (pres, K) => {
    const s = pres.addSlide();
    K.header(s, 'Early check: timing', 'Corn prices move before the drought data is published');
    const labels = D.lags.map((l) => {
      const n = Math.abs(l.k);
      const base = l.k === 0 ? 'Publication\nweek' : `${n} wk${n === 1 ? '' : 's'}\n${l.k < 0 ? 'before' : 'after'}`;
      return l.stars ? `${base}\n${l.stars}` : base;
    });
    const colors = D.lags.map((l) => (l.k < 0 ? C.green : C.gray));
    s.addChart(pres.charts.BAR, [{ name: 'Correlation', labels, values: D.lags.map((l) => l.r) }], Object.assign(axisText(), {
      x: M, y: 1.8, w: 7.9, h: 4.55, barDir: 'col', barGapWidthPct: 50,
      chartColors: colors, invertedColors: colors,
      showValue: true, dataLabelPosition: 'outEnd', dataLabelFormatCode: '+0.00;-0.00;0.00',
      dataLabelColor: C.ink, dataLabelFontFace: BODY, dataLabelFontSize: 13, dataLabelFontBold: true,
      valAxisHidden: true, valAxisMinVal: 0, valAxisMaxVal: 0.17, valGridLine: { style: 'none' }, catGridLine: { style: 'none' },
      catAxisLabelColor: C.ink, catAxisLabelRotate: 0, catAxisLineShow: true, catAxisLineColor: C.line, showLegend: false,
    }));
    K.text(s, 'Weekly corn return, timed relative to publication of the drought change', { x: M, y: 6.38, w: 7.9, h: 0.35, fontSize: 12, color: C.muted, align: 'center', valign: 'middle' });
    const x = 8.95;
    const w = 3.78;
    K.text(s, '1–2 weeks', { x, y: 1.85, w, h: 0.8, fontFace: BODY, fontSize: 44, bold: true, color: C.green, valign: 'middle' });
    K.text(s, 'Corn prices lead the Drought Monitor, even with all leads and lags estimated jointly and 2012 excluded', { x, y: 2.67, w, h: 0.95 });
    K.text(s, `${fmtR(a.r_corn)} → ${fmtR(a.r_corn_ex2012)}`, { x, y: 3.75, w, h: 0.75, fontFace: BODY, fontSize: 34, bold: true, color: C.red, valign: 'middle' });
    K.text(s, 'The annual link between summer stress and corn returns vanishes without 2012', { x, y: 4.52, w, h: 0.75 });
    K.card(s, x, 5.4, w, 1.3, C.dark);
    K.text(s, 'The market may already know. The project measures how early, and whether better data closes the gap.',
      { x: x + 0.25, y: 5.52, w: w - 0.5, h: 1.06, color: C.white, valign: 'middle' });
    K.footer(s, 'Growing seasons (May–September) 2000–2025, about 555 weeks per estimate.   *** p < 0.01   ** p < 0.05   * p < 0.10');
    return s;
  },

  value: (pres, K, I) => {
    const s = pres.addSlide();
    K.header(s, 'Where the value comes from', 'BCG\u2019s 10-20-70 rule, applied to this project');
    const cards = [
      ['10%', 'Algorithms', 'Lead-lag regressions and event studies, kept simple so the result is auditable.', I.calcGreen, false],
      ['20%', 'Data and technology', 'Free public sources, a re-runnable pipeline, and point-in-time publication dates. Where most of my semester goes.', I.dbGreen, false],
      ['70%', 'People and process', 'Traders, procurement and insurers: whose decision changes, and when. A transparent score they can audit beats a black box they cannot.', I.peopleWhite, true],
    ];
    const cw = (CW - 2 * 0.3) / 3;
    cards.forEach(([share, label, body, icon, strong], i) => {
      const x = M + i * (cw + 0.3);
      const y = 1.9;
      K.card(s, x, y, cw, 3.25, strong ? C.dark : C.panel);
      K.iconCircle(s, icon, x + 0.3, y + 0.3, 0.72, strong ? C.green : C.greenSoft, 0.22);
      K.text(s, share, { x: x + 1.2, y: y + 0.26, w: cw - 1.5, h: 0.8, fontSize: 40, bold: true, color: strong ? C.gold : (i === 0 ? C.muted : C.green), valign: 'middle' });
      K.text(s, label, { x: x + 0.3, y: y + 1.22, w: cw - 0.6, h: 0.5, fontSize: 18, bold: true, color: strong ? C.white : C.ink });
      K.text(s, body, { x: x + 0.3, y: y + 1.76, w: cw - 0.6, h: 1.35, fontSize: 15, color: strong ? C.mist : C.muted });
    });
    K.card(s, M, 5.5, CW, 0.95);
    K.iconCircle(s, I.bulbGreen, M + 0.25, 5.62, 0.7, C.greenSoft, 0.22);
    K.text(s, 'The early checks say the market may already price the weather. 10-20-70 says that was only the 10%.',
      { x: M + 1.15, y: 5.5, w: CW - 1.45, h: 0.95, fontSize: 16, valign: 'middle' });
    K.footer(s, 'BCG: value from AI comes roughly 10% from algorithms, 20% from data and technology, and 70% from people and process.');
    return s;
  },

  approach: (pres, K, I) => {
    const s = pres.addSlide();
    K.header(s, 'Approach', 'From weather, to a validated risk score, to price tests — phases follow CRISP-ML(Q)');
    const steps = [
      [I.mapGreen, 'Map exposure', 'Weight locations by county production from USDA, replacing equal weights'],
      [I.heatGreen, 'Measure stress', 'Heat above crop thresholds, rainfall and drought in each crop’s sensitive window'],
      [I.calcGreen, 'Build the risk score', 'Validate it against crop yields before connecting it to prices'],
      [I.chartGreen, 'Link to prices', 'Lead-lag regressions and event studies, controlling for seasonality, USDA reports and contract rolls'],
      [I.globeGreen, 'Extend', 'Forecasts, cocoa and coffee, and agribusiness stocks, as time allows'],
    ];
    const gap = 0.25;
    const w = (CW - 4 * gap) / 5;
    const d = 0.6;
    const cy = 2.05;
    s.addShape(pres.shapes.LINE, { x: M + w / 2, y: cy + d / 2, w: 4 * (w + gap), h: 0, line: { color: C.line, width: 2 } });
    steps.forEach(([icon, title, body], i) => {
      const x = M + i * (w + gap);
      K.numCircle(s, i + 1, x + w / 2 - d / 2, cy, d, i === 4 ? C.gray : C.dark, C.white, 18);
      K.card(s, x, 2.9, w, 3.0);
      s.addImage({ data: icon, x: x + 0.25, y: 3.1, w: 0.5, h: 0.5 });
      K.text(s, title, { x: x + 0.25, y: 3.75, w: w - 0.45, h: 0.62, fontSize: 16, bold: true });
      K.text(s, body, { x: x + 0.25, y: 4.4, w: w - 0.45, h: 1.4, fontSize: 13, color: C.muted });
    });
    K.text(s, 'GUARDRAILS', { x: M, y: 6.12, w: 1.6, h: 0.5, fontSize: 12, bold: true, color: C.muted, charSpacing: 2, valign: 'middle' });
    ['Point-in-time: data as of its publication date', 'Out-of-sample, expanding-window tests', 'Every result re-run without 2012'].forEach((r, i) => {
      const x = 2.25 + i * 3.48;
      K.iconCircle(s, I.checkWhite, x, 6.17, 0.4, C.green, 0.26);
      K.text(s, r, { x: x + 0.5, y: 6.12, w: 2.95, h: 0.5, fontSize: 13, valign: 'middle' });
    });
    return s;
  },

  plan: (pres, K) => {
    const s = pres.addSlide();
    K.header(s, 'Semester plan', 'Data first, then the score, then the price tests');
    const start = Date.UTC(2026, 8, 14);
    const days = 91;
    const x0 = 3.8;
    const x1 = W - M;
    const dx = (mo, day) => x0 + ((Date.UTC(2026, mo - 1, day) - start) / 86400000) * ((x1 - x0) / days);
    const top = 1.9;
    const rowH = 0.7;
    const bottom = top + 0.5 + 6 * rowH;
    [['September', dx(9, 14), dx(10, 1)], ['October', dx(10, 1), dx(11, 1)], ['November', dx(11, 1), dx(12, 1)], ['December', dx(12, 1), x1]]
      .forEach(([name, lo, hi], i) => {
        if (i % 2 === 1) s.addShape(pres.shapes.RECTANGLE, { x: lo, y: top, w: hi - lo, h: bottom - top, fill: { color: C.panel }, line: { color: C.panel, width: 0.75 } });
        K.text(s, name, { x: lo, y: top + 0.05, w: hi - lo, h: 0.4, fontSize: 13, bold: true, color: C.muted, align: 'center', valign: 'middle' });
      });
    const phases = [
      ['Data pipeline', 'Automated, re-runnable ingestion', [9, 16], [10, 4], 'core'],
      ['Exposure and stress', 'County weights, heat, drought', [9, 28], [10, 25], 'core'],
      ['Risk score', 'Build it and validate on yields', [10, 19], [11, 8], 'core'],
      ['Price tests', 'Lead-lag and event studies', [11, 2], [11, 29], 'core'],
      ['Extensions', 'Forecasts, cocoa, coffee, stocks', [11, 16], [12, 6], 'stretch'],
      ['Findings', 'Write-up and final presentation', [11, 30], [12, 11], 'final'],
    ];
    phases.forEach(([name, desc, from, to, kind], i) => {
      const y = top + 0.5 + i * rowH;
      K.text(s, [
        { text: name, options: { bold: true, fontSize: 15, breakLine: true } },
        { text: desc, options: { fontSize: 12, color: C.muted } },
      ], { x: M, y, w: 3.05, h: rowH - 0.08, valign: 'middle' });
      const bx = dx(...from);
      const bar = { x: bx, y: y + 0.17, w: dx(...to) - bx, h: 0.36, rectRadius: 0.08 };
      if (kind === 'core') Object.assign(bar, { fill: { color: C.green }, line: { color: C.green, width: 0.75 } });
      if (kind === 'stretch') Object.assign(bar, { fill: { color: C.white }, line: { color: C.green, width: 1.5, dashType: 'dash' } });
      if (kind === 'final') Object.assign(bar, { fill: { color: C.gold }, line: { color: C.gold, width: 0.75 } });
      s.addShape(pres.shapes.ROUNDED_RECTANGLE, bar);
    });
    const nowX = dx(9, 15);
    s.addShape(pres.shapes.LINE, { x: nowX, y: top + 0.45, w: 0, h: bottom - top - 0.45, line: { color: C.red, width: 1.5, dashType: 'dash' } });
    K.text(s, 'Proposal', { x: nowX + 0.08, y: bottom - 0.38, w: 1.2, h: 0.3, fontSize: 11, bold: true, color: C.red, valign: 'middle' });
    return s;
  },

  risks: (pres, K, I) => {
    const s = pres.addSlide();
    K.header(s, 'Risks and mitigations', 'What could go wrong, and why the project still delivers');
    const items = [
      ['The market already knows', 'How early the market knows is itself a finding; test forecasts and slower markets like cocoa'],
      ['One extreme year dominates', 'Report every result with and without 2012, and add crops and regions for more events'],
      ['Observed weather lags forecasts', 'Track publication lags from the start; add forecast data as an extension'],
      ['Futures prices jump at contract rolls', 'Cross-check against the CORN fund and control for roll dates'],
      ['Five points stand in for the corn belt', 'Weight counties by USDA production data'],
    ];
    K.text(s, 'RISK', { x: M, y: 1.82, w: 3, h: 0.3, fontSize: 12, bold: true, color: C.muted, charSpacing: 2, valign: 'middle' });
    K.text(s, 'MITIGATION', { x: 6.3, y: 1.82, w: 3, h: 0.3, fontSize: 12, bold: true, color: C.muted, charSpacing: 2, valign: 'middle' });
    items.forEach(([risk, fix], i) => {
      const y = 2.2 + i * 0.92;
      K.card(s, M, y, 5.0, 0.8, C.redSoft);
      s.addImage({ data: I.warnRed, x: M + 0.25, y: y + 0.2, w: 0.4, h: 0.4 });
      K.text(s, risk, { x: M + 0.85, y, w: 4.0, h: 0.8, fontSize: 16, bold: true, valign: 'middle' });
      s.addImage({ data: I.arrowGray, x: 5.78, y: y + 0.26, w: 0.32, h: 0.28 });
      K.card(s, 6.3, y, W - M - 6.3, 0.8);
      s.addImage({ data: I.checkGreen, x: 6.55, y: y + 0.22, w: 0.36, h: 0.36 });
      K.text(s, fix, { x: 7.12, y, w: W - M - 7.12 - 0.2, h: 0.8, fontSize: 14, valign: 'middle' });
    });
    return s;
  },

  close: (pres, K, I) => {
    const s = pres.addSlide();
    s.background = { color: C.dark };
    K.text(s, 'THE QUESTION', { x: M, y: 1.35, w: 6, h: 0.35, fontSize: 13, bold: true, color: C.gold, charSpacing: 2, valign: 'middle' });
    K.text(s, 'Does the futures market price weather before the data does?', { x: M, y: 1.8, w: 7.3, h: 2.6, fontFace: HEAD, fontSize: 40, bold: true, color: C.white });
    [
      [I.rulerWhite, 'Measure', ['Weather stress, validated', 'against crop yields']],
      [I.stopwatchWhite, 'Time', ['Market moves, against', 'publication dates']],
      [I.bulbWhite, 'Test', ['Whether a risk score', 'adds anything new']],
    ].forEach(([icon, head, sub], i) => {
      const y = 1.8 + i * 1.3;
      K.iconCircle(s, icon, 8.6, y + 0.05, 0.85, C.green);
      K.text(s, [
        { text: head, options: { bold: true, fontSize: 18, color: C.white, breakLine: true } },
        { text: sub[0], options: { fontSize: 14, color: C.mist, breakLine: true } },
        { text: sub[1], options: { fontSize: 14, color: C.mist } },
      ], { x: 9.65, y, w: W - M - 9.65, h: 0.95, valign: 'middle' });
    });
    K.text(s, 'Questions?', { x: M, y: 5.0, w: 6, h: 0.7, fontFace: HEAD, fontSize: 32, bold: true, color: C.gold, valign: 'middle' });
    s.addImage({ data: I.githubMist, x: M, y: 5.97, w: 0.32, h: 0.32 });
    K.text(s, 'github.com/haydn-s/climate-futures-pricing', { x: M + 0.45, y: 5.95, w: 6.5, h: 0.36, fontSize: 15, color: C.mist, valign: 'middle' });
    return s;
  },
};

// Deck order, as presented. Reorder these keys to reorder the deck.
const ORDER = ['title', 'problem', 'questions', 'stakeholders', 'datasets', 'measurement',
  'case2012', 'timing', 'value', 'approach', 'plan', 'risks', 'close'];
const SLIDES = ORDER.map((key) => SLIDE_FNS[key]);
const NOTES = ORDER.map((key) => NOTECARDS[key]);

// ------------------------------------------------------------------ build
async function writeDeck(file, indices, I) {
  const pres = new pptxgen();
  pres.layout = 'LAYOUT_WIDE';
  pres.title = 'Climate Futures Pricing';
  pres.subject = 'Duke AIPI 590 project proposal';
  pres.author = 'Haydn Stucker';
  const K = kit(pres);
  for (const i of indices) {
    const s = SLIDES[i](pres, K, I);
    if (i > 0) K.number(s, i === SLIDES.length - 1);
    s.addNotes(NOTES[i]);
  }
  // Scatter series leave empty values where a point is absent; drop them so PowerPoint treats them as blanks.
  const zip = await JSZip.loadAsync(await pres.write({ outputType: 'nodebuffer' }));
  for (const name of Object.keys(zip.files).filter((n) => /^ppt\/charts\/chart\d+\.xml$/.test(n))) {
    const xml = await zip.file(name).async('string');
    let out = xml.replace(/<c:pt idx="\d+"><c:v><\/c:v><\/c:pt>/g, '');
    if (out.includes('<c:scatterChart>')) {
      const idx = D.scatter.year.indexOf(2012);
      const dPt = `<c:dPt><c:idx val="${idx}"/><c:marker><c:symbol val="circle"/><c:size val="13"/><c:spPr>`
        + `<a:solidFill><a:srgbClr val="${C.red}"/></a:solidFill><a:ln w="9525"><a:solidFill><a:srgbClr val="${C.red}"/></a:solidFill></a:ln>`
        + '</c:spPr></c:marker><c:bubble3D val="0"/></c:dPt>';
      if ((out.match(/<\/c:marker>/g) || []).length !== 1) throw new Error('expected one scatter series marker');
      out = out.replace('</c:marker>', '</c:marker>' + dPt);
    }
    zip.file(name, out);
  }
  fs.writeFileSync(file, await zip.generateAsync({ type: 'nodebuffer', compression: 'DEFLATE' }));
}

(async () => {
  if (ORDER.length !== Object.keys(SLIDE_FNS).length) throw new Error('every slide must appear in ORDER exactly once');
  if (new Set(ORDER).size !== ORDER.length) throw new Error('duplicate key in ORDER');
  if (SLIDES.some((fn) => !fn) || NOTES.some((note) => !note)) throw new Error('ORDER names a slide or notecard that does not exist');
  const [target, mode] = process.argv.slice(2);
  const I = await renderIcons();
  if (mode === '--single') {
    fs.mkdirSync(target, { recursive: true });
    for (let i = 0; i < SLIDES.length; i++) await writeDeck(path.join(target, `slide-${String(i + 1).padStart(2, '0')}.pptx`), [i], I);
  } else {
    await writeDeck(target, SLIDES.map((_, i) => i), I);
  }
  console.log('wrote', target);
})().catch((e) => { console.error(e); process.exit(1); });
