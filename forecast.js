/* forecast.js - Rechenkern der Surfwelle-Prognose.
 *
 * Reine Rechenlogik ohne Seite/DOM: Kennlinien, Laufzeiten, Einlesen der
 * CSV-Daten, Sondenfehler-Filter, Stundenprognose und 7-Tage-Aussicht.
 * Wird von index.html (Browser, <script src>) UND von notify.js
 * (GitHub Actions, Node) genutzt - so rechnen Seite und Push-Nachrichten
 * garantiert gleich. Änderungen an der Prognose gehören hierher.
 */
/* ---------- Kalibrierung ---------- */
// Türkheim -> Kanal [Median, P20, P80], aus 322 Tagen Fabrikkanal-Messdaten.
// Die beiden untersten Punkte stammen aus dem Niedrigwasser Juli 2026 - ohne
// sie läuft die Kurve unterhalb von 3 m³/s flach aus und kann den Einbruch
// bei Rekordniedrigwasser nicht abbilden.
// Vier-Stufen-Einteilung der Wellenhöhe, nach dem Vorbild von
// surfwelleaugsburg.de: <57 nicht surfbar, 57-65 schwach/"für Verrückte",
// 65-75 ausreichend, >=75 stark. "Surfbar" im Sinne des Modells (Prognose,
// Skill-Auswertung, 7-Tage-Kacheln) bedeutet ab jetzt >=57cm statt bisher 60.
const TIERS = [
  [0,  '#DC3545', 'nicht surfbar'],
  [57, '#FFC107', 'wenig Wasser'],
  [65, '#0D6EFD', 'ausreichend'],
  [75, '#198754', 'viel Wasser']
];
const SURF_CM = 57;
const tierOf = cm => { let t=TIERS[0]; for (const x of TIERS) if (cm>=x[0]) t=x; return t; };

const TUE2FAB = [
  [2.05, 3.64, 3.14, 4.12],[2.67, 3.60, 3.10, 4.68],
  [3.05, 4.06, 3.54, 5.04],[4.58, 5.96, 4.76, 7.09],[5.52, 7.42, 6.10, 8.43],
  [6.43, 9.00, 7.88,10.57],[7.39,10.66, 9.32,11.95],[8.45,12.31,10.80,13.79],
  [9.47,13.31,11.68,15.44],[10.80,15.08,13.73,17.30],[13.10,18.11,15.43,20.24],
  [17.40,22.29,19.62,22.98],[27.60,23.01,22.96,23.50]
];
// Kanal -> [Wellenhöhe cm, P(Welle >= 57cm)] — neu kalibriert auf die
// 57-cm-Surfschwelle (vorher 60), aus demselben 73-Tage-Vergleich.
const FAB2WAVE = [
  [0.62, 8.0,0.000],[2.89,20.6,0.000],
  [3.86,19.3,0.000],[5.49,26.6,0.000],[6.79,33.8,0.000],[8.11,41.5,0.013],
  [9.08,48.0,0.086],[10.60,57.5,0.542],[11.30,62.3,0.930],[12.31,63.5,0.960],
  [13.51,67.5,1.000],[14.90,76.0,1.000],[16.80,81.5,1.000],
  // Sättigung aus dem Nov-2026-Hochwasser: ab ~18,5 m³/s Kanal stabil 83-86 cm
  [18.50,83.5,1.000],[23.00,85.0,1.000]
];
// Sebastianskapelle -> Türkheim 10 h später [Median, P20, P80], r = 0.846
const SEBA2TUE = [
  [0.49, 5.43, 3.70, 6.38],[0.95, 7.41, 6.38, 9.19],[1.44, 9.29, 7.23,11.90],
  [2.15,11.20, 9.06,16.80],[3.06,14.60,10.70,21.90],[4.91,20.50,12.00,28.70],
  [8.99,30.50,21.74,50.40],[15.00,48.80,35.10,88.00]
];
// 24h-Regenprognose Oberjoch -> Türkheim-Anstieg binnen 36 h [Median, P80].
// Aus 1482 Stunden Mai–Juli 2026. Die P80-Grenze wurde in 80 % der Stunden
// eingehalten, der Median in 50 %. Bewusst nur als Spanne: die Prognose
// erklaert rund 20 % der Streuung, das traegt keine Linie und erst recht
// keinen Zeitpunkt. Zwei winzige Nicht-Monotonien geglaettet.
const RAIN2RISE = [[0,0.66,1.39],[1.8,1.24,2.59],[4.1,1.57,2.96],
                   [9.0,1.60,4.31],[16.7,2.55,4.85],[31.4,2.55,5.05]];
const RAIN_MIN = 3;   // mm, ab hier lohnt die Aussage

// Laufzeiten folgen dem Potenzgesetz T = T10 · (Q/10)^-b. Über den ganzen
// Jahresdatensatz gemessen, für jeden Flussabschnitt einzeln:
//   Thalhofen→Biessenhofen   b=0,384  T10=1,97 h   R²=0,99
//   Biessenhofen→Türkheim    b=0,311  T10=4,84 h   R²=0,91
//   Sebastianskap.→Thalhofen b=0,271  T10=5,55 h   R²=0,98
//   Türkheim→Fabrikkanal     b=0,300  T10=4,98 h   R²=0,92
//   Türkheim→Welle gesamt    b=0,401  T10=5,71 h   R²=0,98
// Die Exponenten liegen alle zwischen 0,27 und 0,40 - der Bereich, den die
// kinematische Wellentheorie für natürliche Gerinne erwartet. Bei viel Wasser
// fließt es schneller, und zwar in jedem Abschnitt nach demselben Gesetz.
const TUE2WAVE = {t10:5.71, b:0.401};
// Sebastianskapelle → Türkheim als Summe der drei Teilstrecken. Die direkte
// Messung ist unbrauchbar (r < 0,4 außer im Hochwasser), weil der Grüntensee
// dazwischenliegt; die Teilstrecken sind einzeln sauber messbar.
const SEBA2TUE_T = {t10:12.36, b:0.32};
const travel=(p,Q)=>p.t10*Math.pow(Math.min(Math.max(Q,2),40)/10, -p.b);

// Haslach Werksabfluss -> Türkheim, aus 42 Abgabestufen des Grüntensees
// (Jul 2025 - Nov 2026): Haslach->Biessenhofen T10=8,4 h b=0,33 plus
// Biessenhofen->Türkheim T10=4,84 h; direkt gemessen median 14 h.
// Backtest 12 h voraus (Türkheim + Haslach-Änderung): MAE 1,05 m³/s gegen
// 1,24 beim alten Sebastianskapelle-Pfad und 1,09 bei reiner Persistenz.
const HAS2TUE_T = {t10:13.2, b:0.32};
// Überstau-Abbau des Grüntensees (Median der fallenden Tage seit 2015),
// [ab Seestand m ü. NN, cm pro Tag]. Seefläche aus Zufluss/Abfluss-Bilanz.
const SEE_DRAIN = [[876.10,2],[876.30,11],[876.50,25],[879.00,18]];
const SEE_AREA_M2 = 1.15e6;
const SEBA_WARN = 4.0;       // m³/s
const BIAS_CM = -2.9;        // Kennlinien sind Mediane; gegen 73 Tage Messung +2,9 cm
// Grüntensee: SEE_MAX ist das tatsaechliche Stauziel. Tageswerte 1969-2026
// (GKD 12403000): nach jeder Hochwasserspitze kehrt der See auf 876,02-876,08
// zurueck, seit 1990 in jedem Monat das haeufigste Halteniveau. Darueber =
// Ueberstau, der ueber die folgenden Tage gedrosselt abgegeben wird.
// SEE_MIN bleibt die untere Kante der Bewirtschaftungslamelle (WWA Kempten).
const SEE_MIN = 875.50, SEE_MAX = 876.05;
const seePct = m => (m-SEE_MIN)/(SEE_MAX-SEE_MIN)*100;

function interp(t,x,c){
  if (x<=t[0][0]) return t[0][c];
  const l=t[t.length-1]; if (x>=l[0]) return l[c];
  for (let i=0;i<t.length-1;i++){
    const a=t[i],b=t[i+1];
    if (x>=a[0]&&x<=b[0]) return a[c]+(x-a[0])/(b[0]-a[0])*(b[c]-a[c]);
  }
  return l[c];
}
const fabFromTue=q=>interp(TUE2FAB,q,1), fabLo=q=>interp(TUE2FAB,q,2), fabHi=q=>interp(TUE2FAB,q,3);
const waveFromFab=q=>interp(FAB2WAVE,q,1), probFromFab=q=>interp(FAB2WAVE,q,2);
const tueFromSeba=q=>interp(SEBA2TUE,q,1), tueSebaLo=q=>interp(SEBA2TUE,q,2), tueSebaHi=q=>interp(SEBA2TUE,q,3);
const lagHours=q=>travel(TUE2WAVE,q);   // Türkheim -> Welle, gesamt

/* ---------- Zeit ---------- */
const HOUR=3600e3;
function berlinOffsetMs(t){
  const d=new Date(t);
  return new Date(d.toLocaleString('en-US',{timeZone:'Europe/Berlin'}))
       - new Date(d.toLocaleString('en-US',{timeZone:'UTC'}));
}
function parseTime(s){
  s=s.trim();
  if (/([+-]\d{2}:?\d{2}|Z)$/.test(s)) return Date.parse(s);
  const p=Date.parse(s+'Z');
  return p-berlinOffsetMs(p);
}
const fmtTime=new Intl.DateTimeFormat('de-DE',{timeZone:'Europe/Berlin',hour:'2-digit',minute:'2-digit'});
const fmtDay =new Intl.DateTimeFormat('de-DE',{timeZone:'Europe/Berlin',weekday:'short',day:'2-digit',month:'2-digit'});
const berlinHour=t=>+new Intl.DateTimeFormat('en-GB',{timeZone:'Europe/Berlin',hour:'2-digit',hour12:false}).format(t);
const berlinMin =t=>+new Intl.DateTimeFormat('en-GB',{timeZone:'Europe/Berlin',minute:'2-digit'}).format(t);
const stamp=t=>fmtDay.format(t)+' '+fmtTime.format(t);

// NEU: Nahbereichs-Regen (Ackermannwehr/Augsburg, Bobingen) als Risiko-Hinweis,
// NICHT als kalibrierte Kurve wie RAIN2RISE. Der Alpine Pfad braucht 4,5-9h vom
// Tuerkheim-Pegel; Regen, der direkt bei Goeggingen/Bobingen faellt, kann den
// Fabrikkanal/Senkelbach vermutlich in 1-3h erreichen - aber dafuer gibt es
// noch keinen einzigen ausgewerteten Fall, nur die Vermutung. Deshalb hier
// bewusst kein Kennlinien-Fit, sondern nur eine Schwelle mit Zeitfenster.
function localRainOf(d){
  const a=isFinite(d.rainAug)?d.rainAug:-Infinity;
  const b=isFinite(d.rainBob)?d.rainBob:-Infinity;
  const m=Math.max(a,b);
  return isFinite(m)?m:NaN;
}
const LOCAL_RAIN_MIN=3;        // mm in der letzten Stunde, ab hier eine Meldung wert
const LOCAL_RAIN_LAG_LO=1, LOCAL_RAIN_LAG_HI=3;  // h, geschaetzt, nicht kalibriert
const LOCAL_RAIN_WINDOW_H=3;   // wie lange eine Meldung noch als "aktuell" zaehlt

function parseWaveCsv(text){
  const raw=[];
  for (const line of text.split('\n').slice(1)){
    const f=line.trim().split(',');
    if (f.length<2) continue;
    const t=parseTime(f[0]), v=parseFloat(f[1]);
    if (isFinite(t)&&isFinite(v)) raw.push({t,v});
  }
  raw.sort((a,b)=>a.t-b.t);
  return despikeWave(raw);
}

function despikeWave(raw){
  // Sensor-Artefakte: Sprung ueber 30 cm und binnen einer Stunde zurueck auf
  // dasselbe Niveau. Echte Wellen bewegen sich langsamer - ueber den gesamten
  // Datenbestand liegt das 99,9-Perzentil der Schrittaenderung bei 21 cm.
  const out=[];
  for (let i=0;i<raw.length;i++){
    let bad=false;
    for (let a=Math.max(0,i-12); a<i && !bad; a++){
      if (Math.abs(raw[i].v-raw[a].v)<=30) break;
      for (let b=i+1; b<Math.min(raw.length,i+13); b++){
        if (raw[b].t-raw[a].t > 70*60e3) break;
        if (Math.abs(raw[b].v-raw[a].v)<=12){ bad=true; break; }
      }
    }
    if (!bad) out.push(raw[i]);
  }
  return out;
}

// Sondenfehler: Die Wellensonde kippt zeitweise in einen Fehlermodus und
// liefert dann verrauschte Werte um 15-35 cm (auch Nullen), obwohl die Welle
// voll läuft - und springt später wieder auf den richtigen Wert. Verworfen
// wird ein Punkt, wenn er mehr als 35 cm neben dem aus Türkheim erwarteten
// Wert liegt UND das Signal zappelt (Median der |2. Differenz| im Fenster
// +-30 min >= 3 cm). Echte Abweichungen durch Wehreingriffe verlaufen glatt
// und bleiben erhalten. Gegen die Historie (Mai-Nov 2026): Fehlerphase
// 21./22.11. zu 89 % erkannt, im Normalbetrieb kein Punkt verworfen.
// Gleiche Logik serverseitig in sensor_filter.py.
function filterSensorFaults(wave,coll){
  const arr=coll.filter(d=>isFinite(d.tue))
    .map(d=>({t:d.t+lagHours(d.tue)*HOUR, v:waveFromFab(fabFromTue(d.tue))}))
    .sort((a,b)=>a.t-b.t);
  if (arr.length<2||wave.length<3) return {wave, fault:null, nFlagged:0};
  let j=0;
  const expAt=t=>{
    if (t<arr[0].t||t>arr[arr.length-1].t) return NaN;
    while (j<arr.length-2 && arr[j+1].t<t) j++;
    const a=arr[j], b=arr[j+1];
    if (b.t-a.t>3*HOUR) return NaN;
    return a.v+(t-a.t)/Math.max(1,b.t-a.t)*(b.v-a.v);
  };
  const med=x=>{const s=[...x].sort((a,b)=>a-b); return s[Math.floor(s.length/2)];};
  const d2=wave.map((d,i)=>i<2?NaN:Math.abs(d.v-2*wave[i-1].v+wave[i-2].v));
  let lo=0, hi=0;
  const rough=wave.map((d,i)=>{
    while (wave[lo].t<d.t-30*60e3) lo++;
    while (hi<wave.length && wave[hi].t<=d.t+30*60e3) hi++;
    const w=d2.slice(Math.max(lo,2),hi);
    return w.length?med(w):0;
  });
  const keep=[], res=[], flagged=[], info=[];
  wave.forEach((d,i)=>{
    const e0=expAt(d.t);
    if (!isFinite(e0)){ info.push({d, bad:false, dev:0}); return; }
    const bias=res.length>=12?med(res.slice(-200)):BIAS_CM;
    const exp=e0+bias, dev=d.v-exp;
    const bad=Math.abs(dev)>35 && rough[i]>=3;
    info.push({d, bad, dev, exp});
    if (!bad) res.push(d.v-e0);
  });
  // Übergangswerte beim Umkippen der Sonde (z. B. 34 / 49 cm) mitverwerfen:
  // über 30 cm daneben und höchstens 30 min von einem Fehlerwert entfernt.
  const badT=info.filter(x=>x.bad).map(x=>x.d.t);
  info.forEach(x=>{
    if (!x.bad && Math.abs(x.dev)>30 && badT.some(t=>Math.abs(t-x.d.t)<=30*60e3)) x.bad=true;
  });
  for (const x of info){
    if (x.bad) flagged.push({...x.d, exp:x.exp}); else keep.push(x.d);
  }
  const last=wave[wave.length-1];
  const lastBad=flagged.length&&flagged[flagged.length-1].t===last.t?flagged[flagged.length-1]:null;
  // Meldung nur, solange die Sonde gerade im Fehlermodus ist
  let fault=null;
  if (lastBad){
    const lastGood=keep.length?keep[keep.length-1]:null;
    const recent=flagged.filter(x=>x.t>last.t-HOUR);
    fault={since:lastGood?lastGood.t:flagged[0].t, raw:med(recent.map(x=>x.v)), exp:lastBad.exp};
  }
  return {wave:keep, fault, nFlagged:flagged.length};
}


/* ---------- Prognose ---------- */
// Jeder Messwert wird um seine eigene Laufzeit vorwärts abgebildet. Weil die
// Laufzeit am Abfluss hängt und Türkheim in Stufen meldet, sind die Ankünfte
// ungleichmäßig verteilt - bei einem Stufensprung entstehen Lücken von über
// einer Stunde. Deshalb wird die Kurve anschließend auf ein festes Raster
// interpoliert; sonst reißt die Linie an genau diesen Stellen ab.
const GRID = 10*60e3;   // 10 Minuten

function resample(pts, keys){
  if (pts.length<2) return pts;
  const out=[];
  const first=Math.ceil(pts[0].t/GRID)*GRID;
  const last =Math.floor(pts[pts.length-1].t/GRID)*GRID;
  let i=0;
  for (let t=first; t<=last; t+=GRID){
    while (i<pts.length-2 && pts[i+1].t<t) i++;
    const a=pts[i], b=pts[i+1];
    const f=(b.t===a.t)?0:(t-a.t)/(b.t-a.t);
    const o={t};
    for (const k of keys) o[k]=a[k]+f*(b[k]-a[k]);
    out.push(o);
  }
  return out;
}

function buildForecast(coll, wave, ref){
  const rawNear=[], far=[];
  // Ueberholeffekt: die Laufzeit haengt vom Abfluss ab, also kann ein spaeter
  // gemessener, hoeherer (= schnellerer) Wert einen frueher gemessenen,
  // niedrigeren (= noch unterwegs befindlichen) Wert bei der Ankunft
  // ueberholen. Physikalisch verschluckt die schnellere Welle die langsamere
  // dann, statt dass beide getrennt ankommen - deshalb werden ueberholte
  // Punkte hier verworfen statt als eigene, falsche Spitze/Delle stehen zu
  // bleiben. coll ist bereits chronologisch nach Erfassungszeit sortiert.
  for (const r of coll){
    if (isFinite(r.tue)){
      const lag=lagHours(r.tue)*HOUR;
      const pt={t:r.t+lag,
        mid:waveFromFab(fabFromTue(r.tue)),
        lo :waveFromFab(fabLo(r.tue)),
        hi :waveFromFab(fabHi(r.tue)),
        p  :probFromFab(fabFromTue(r.tue))};
      while (rawNear.length && rawNear[rawNear.length-1].t>=pt.t) rawNear.pop();
      rawNear.push(pt);
    }
  }

  // Erst auf ein festes Raster bringen, dann den Versatz messen: die rohen
  // Ankunftszeiten sind ungleichmäßig verteilt (siehe resample), in einem
  // Ein-Stunden-Fenster kann sonst kein einziger Punkt liegen.
  const keys=['mid','lo','hi','p'];
  const gridNear=resample(rawNear,keys);

  // Selbstkalibrierung: den systematischen Versatz aus der jüngsten
  // Vergangenheit messen, statt eine feste Konstante mitzuschleppen. Drei
  // Fenster - eine Stunde mit halbem Gewicht für schnelle Einbrüche, sechs
  // Stunden und drei Tage als Anker gegen Rauschen. Über 73 Tage
  // zurückgerechnet: MAE 3,4 statt 6,4 cm, Präzision der Surfbar-Entscheidung
  // 85 statt 64 %, Fehlalarme 59 statt 156. Ein Mittelwert hinkt einer
  // fallenden Welle immer um das halbe Fenster hinterher - deshalb das kurze;
  // die längeren verhindern, dass die Korrektur Ausreißern nachläuft.
  const measure=(hours,minN)=>{
    const from=ref-hours*HOUR;
    const past=gridNear.filter(d=>d.t>=from&&d.t<ref);
    if (past.length<3||!wave.length) return null;
    let sum=0,n=0,j=0;
    for (const w of wave){
      if (w.t<from||w.t>=ref) continue;
      while (j<past.length-2 && Math.abs(past[j+1].t-w.t)<=Math.abs(past[j].t-w.t)) j++;
      if (Math.abs(past[j].t-w.t)>20*60e3) continue;
      sum+=w.v-past[j].mid; n++;
    }
    return n>=minN ? {b:sum/n, n} : null;
  };
  const win=[[measure(1,4),0.5],[measure(6,12),0.3],[measure(72,100),0.2]];
  let bias=BIAS_CM, biasN=0, biasAuto=false;
  const have=win.filter(w=>w[0]);
  if (have.length){
    // Gewichte auf die tatsächlich verfügbaren Fenster normieren, damit ein
    // fehlendes Fenster die Korrektur nicht abschwächt.
    const wsum=have.reduce((a,w)=>a+w[1],0);
    const b=have.reduce((a,w)=>a+w[0].b*w[1],0)/wsum;
    // Plausibilitätsgrenze: ein Sensorausfall soll das Modell nicht wegziehen.
    if (Math.abs(b)<=25){
      bias=b; biasAuto=true;
      biasN=have.reduce((a,w)=>Math.max(a,w[0].n),0);
    }
  }

  const near=gridNear.map(d=>({t:d.t,
    mid:Math.max(0,d.mid+bias), lo:Math.max(0,d.lo+bias),
    hi :Math.max(0,d.hi+bias),  p:d.p}));

  // Fernpfad aus Haslach (Abfluss direkt unter dem Grüntensee): Was dort
  // gerade mehr oder weniger abfliesst als vor einer Laufzeit, kommt eine
  // Laufzeit spaeter 1:1 in Türkheim dazu. Das Wasser, das jetzt Türkheim
  // passiert, hat Haslach vor L Stunden verlassen - deshalb ist der Bezug
  // Haslach(ref-L), und die Kurve setzt ohne Sprung am aktuellen Stand an.
  const lastT=[...coll].reverse().find(d=>isFinite(d.tue));
  const farMapped=[];
  if (lastT){
    const L=travel(HAS2TUE_T,lastT.tue)*HOUR;
    const hasRows=coll.filter(d=>isFinite(d.has));
    const at=t=>{let best=null;
      for (const d of hasRows) if (!best||Math.abs(d.t-t)<Math.abs(best.t-t)) best=d;
      return best&&Math.abs(best.t-t)<=HOUR?best.has:NaN;};
    const base=at(lastT.t-L);
    if (isFinite(base)){
      for (const d of hasRows){
        if (d.t<=lastT.t-L) continue;
        const tu=Math.max(0.3,lastT.tue+d.has-base);
        const pt={t:d.t+L+lagHours(tu)*HOUR,
          mid:Math.max(0,waveFromFab(fabFromTue(tu))+bias),
          lo :Math.max(0,waveFromFab(fabLo(tu))+bias),
          hi :Math.max(0,waveFromFab(fabHi(tu))+bias),
          p  :probFromFab(fabFromTue(tu))};
        while (farMapped.length && farMapped[farMapped.length-1].t>=pt.t) farMapped.pop();
        farMapped.push(pt);
      }
    }
  }

  return {near, far:resample(farMapped,keys),
          bias, biasN, biasAuto};
}



// Wahrscheinlichkeit, dass das Tagesmaximum die Surfschwelle erreicht,
// direkt empirisch aus 2506 rückgerechneten Tagen: projizierter Türkheim-Wert
// -> Anteil der Tage, an denen das Maximum tatsächlich 7,9 m³/s erreichte.
// Der Umweg über die Residuenverteilung war irreführend - deren linker Rand
// stammt aus Wochen mit viel höherem Abfluss und ergab selbst bei völliger
// Trockenheit noch 19 %. Der Horizont spielt kaum eine Rolle (Tag 1-2 gegen
// Tag 5-7 unterscheidet sich um wenige Prozentpunkte), deshalb eine Tabelle.
const PROJ_P = [[3.0,0.00],[4.22,0.15],[5.01,0.22],[6.02,0.51],
                [7.06,0.78],[8.47,0.91],[10.62,0.99],[18.05,1.00]];
const Q_SURF = 7.67;         // Türkheim für Kanal 11,1 = 57 cm
const SURF_VON = 8, SURF_BIS = 20;   // die Welle darf nur tagsüber gesurft werden

const projProb = mid => mid<=3.0 ? 0 : interp(PROJ_P, mid, 1);

const dayKey=t=>new Intl.DateTimeFormat('en-CA',
  {timeZone:'Europe/Berlin',year:'numeric',month:'2-digit',day:'2-digit'}).format(t);

/* ---------- 7-Tage-Aussicht ---------- */
// Der Gebietsniederschlag wird seit collect.py v1.11 dort schon gewichtet
// (CATCHMENT_DAILY_WEIGHTS: Oberjoch 35%, Bad Wörishofen 30%, Marktoberdorf
// 20%, Nesselwang 15% - Schwerpunkt auf Ober-/Mittellauf, weil Regen
// unterhalb von Türkheim kaum zur Welle beiträgt, r≈0,1).
// Rezession an regenfreien Tagen, gefittet auf 132 Trockentage:
// Q_morgen = Qb + (Q_heute - Qb) · k. Der Basisabfluss verhindert, dass die
// Projektion über eine Woche ins Bodenlose fällt - eine reine Prozentabnahme
// unterschätzte den Wert nach sieben Tagen im Mittel um 3,5 m³/s.
const REC_QB = 3.4, REC_K = 0.862;
// Zwei-Tages-Gebietsniederschlag -> Zuwachs bei Türkheim [mm, m³/s].
// Unter etwa 12 mm passiert netto nichts.
const RAIN2Q = [[0,0],[1.6,-0.24],[4.4,0.20],[8.5,0.09],[17.1,1.53],[30.5,2.59]];
// Unsicherheit je Horizont [Tag, unten, oben] - das mittlere Halbe der
// Residuen aus 357 rückgerechneten Projektionen. Das Band ist bewusst
// asymmetrisch: die Projektion unterschätzt häufiger, als sie überschätzt.
const PROJ_BAND = [[1,-0.47,0.75],[2,-0.61,1.31],[3,-0.57,2.05],[4,-0.58,2.43],
                   [5,-0.41,2.67],[6,-0.46,2.98],[7,-0.54,3.12]];

// Die 7-Tage-Regenprognose steckt seit collect.py v1.11 direkt in recent.csv
// (forecast_area_rain_d0_mm .. d7_mm, bereits gebietsgewichtet). Kein eigener
// Open-Meteo-Aufruf mehr im Browser nötig - das war die vorige, fehleranfällige
// Variante, weil ein CORS-Problem oder eine Downtime bei Open-Meteo sonst
// nur diese eine Kachel lahmlegte, jetzt fällt sie zusammen mit dem Rest der
// Seite unter dieselbe, bereits geprüfte Ladefunktion.
// Nicht jeder 15-Minuten-Lauf bekommt eine Regenprognose von Open-Meteo
// (Nov 2026: in ~18 % der Zeilen leer). Deshalb die jüngste Zeile MIT
// Prognose nehmen, solange sie höchstens 12 h alt ist.
function latestOutlook(coll){
  for (let i=coll.length-1;i>=0;i--){
    const r=coll[i];
    if (coll[coll.length-1].t-r.t>12*3600e3) break;
    if (r.rainStart && r.rainDays && r.rainDays.some(isFinite)) return loadOutlookFromRow(r);
  }
  return null;
}

function loadOutlookFromRow(lastRow){
  if (!lastRow || !lastRow.rainStart || !lastRow.rainDays) return null;
  // Mittags ansetzen statt Mitternacht: sonst kann toISOString() ueber die
  // UTC-Tagesgrenze rutschen und ein Datum zu frueh ausgeben (00:00 MESZ ist
  // 22:00 UTC am Vortag - genau das ist beim ersten Bauen passiert).
  const start=new Date(lastRow.rainStart+'T12:00:00+02:00');
  const out=[];
  lastRow.rainDays.forEach((mm,i)=>{
    if (!isFinite(mm)) return;
    const day=new Date(start.getTime()+i*86400000);
    out.push({day: day.toISOString().slice(0,10), mm});
  });
  return out.length ? out : null;
}

/* ---------- Einlesen recent.csv (ohne fetch, Text rein) ---------- */
function parseCollected(text){
  const lines=text.split('\n');
  const cols=lines[0].trim().split(',');
  const ix=n=>cols.indexOf(n);
  const iT=ix('collected_at'), iTu=ix('tuerkheim_q_m3s'), iSe=ix('sebastianskapelle_q_m3s'),
        iBi=ix('biessenhofen_q_m3s'), iR=ix('forecast_rain_oberjoch_24h_mm'),
        iG=ix('gruentensee_w_mnn'), iHa=ix('haslach_q_m3s'), iStart=ix('forecast_area_rain_start_date'),
        iRA=ix('rain_augsburg_mm'), iRB=ix('rain_bobingen_mm');
  const iDay=[...Array(8)].map((_,k)=>ix(`forecast_area_rain_d${k}_mm`));
  if (iT<0) throw new Error('recent.csv: Spalte collected_at fehlt');
  const out=[];
  for (const line of lines.slice(1)){
    if (!line.trim()) continue;
    const f=line.split(',');
    if (f.length<cols.length-2) continue;
    const t=parseTime(f[iT]);
    if (!isFinite(t)) continue;
    const num=i=>(i>=0&&f[i]!==undefined&&f[i]!==''?parseFloat(f[i]):NaN);
    const str=i=>(i>=0&&f[i]!==undefined&&f[i]!==''?f[i]:null);
    out.push({t, tue:num(iTu), seba:num(iSe), bies:num(iBi), rain24:num(iR), see:num(iG), has:num(iHa),
               rainStart: iStart>=0 ? str(iStart) : null,
               rainDays: iDay.map(i=>num(i)),
               rainAug:num(iRA), rainBob:num(iRB)});
  }
  return out.sort((a,b)=>a.t-b.t);
}

/* ---------- 7-Tage-Aussicht: Rechnung ---------- */
// Liefert je Tag {key, mm, q, i, p, src, when, past} - die Anzeige macht
// renderOutlook() in index.html, die Push-Logik nutzt dieselben Werte.
function computeOutlook(fc, coll, ref, outlook){
  if (!outlook||!outlook.length) return {err:'keine Regenprognose in recent.csv — läuft schon collect.py v1.11?'};
  const lastT=[...coll].reverse().find(d=>isFinite(d.tue));
  if (!lastT) return {err:'kein aktueller Türkheim-Wert'};
  // Mittlerer Pfad, dazu das empirische Unsicherheitsband je Horizont.
  const mid=[]; let q=lastT.tue;
  outlook.forEach((d,i)=>{
    const p2=d.mm+(i>0?outlook[i-1].mm:0);
    // Die Rezession darf den Abfluss nie anheben: bei Werten unterhalb des
    // Basisabflusses würde die Formel sonst nach oben ziehen.
    const rec=Math.min(q, REC_QB+(q-REC_QB)*REC_K);
    q=Math.max(1.2, rec+interp(RAIN2Q,p2,1));
    mid.push(q);
  });

  // --- Wahrscheinlichkeit je Tag ---------------------------------------
  // Für heute und morgen liefert die Stundenprognose aus Türkheim und
  // der Fernpfad den genaueren Wert - sie kennt den Zeitpunkt. Ab
  // übermorgen bleibt nur die Tagesprojektion aus dem Regen.
  const today=dayKey(ref);
  const days=outlook.slice(0,7).map((d,i)=>({key:d.day, mm:d.mm, q:mid[i], i}));
  // Falls Open-Meteo den heutigen Tag nicht als ersten liefert, ausrichten
  while (days.length && days[0].key<today) days.shift();

  const info=days.map((d,i)=>{
    let p=projProb(d.q), src='Regenprognose', when=null;
    // Stundenprognose: nur Punkte im Surffenster, die noch bevorstehen.
    // Gleiche Nah-/Fern-Grenze wie in updateStatus() und render() - sonst
    // kann derselbe Anker-Effekt hier eine falsche Frueh-Ankunft erzeugen.
    const futNearD=fc.near.filter(x=>x.t>=ref);
    const nearEndD=futNearD.length?futNearD[futNearD.length-1].t:ref;
    const pts=fc.near.concat(fc.far.filter(x=>x.t>nearEndD)).filter(x=>{
      if (x.t<ref || dayKey(x.t)!==d.key) return false;
      const h=berlinHour(x.t);
      return h>=SURF_VON && h<SURF_BIS;
    });
    if (pts.length){
      const best=pts.reduce((a,b)=>b.p>a.p?b:a);
      if (best.p>p){ p=best.p; src='Pegelstand'; when=best.t; }
      else if (best.p>0.02) when=best.t;
    }
    // Ein Tag ohne verbleibendes Surffenster fällt raus
    const past = d.key===today && berlinHour(ref)>=SURF_BIS;
    return {...d, p:past?0:p, src, when, past};
  });
  return {info, today, days, mid, lastT};
}

// Gesamte Ausgangslage aus den Rohtexten - für Browser und Node gleich.
// Sondenfehler-Filter, Bezugszeitpunkt und Prognose wie beim Seitenaufbau.
function prepareData(collText, waveText){
  const coll=parseCollected(collText);
  let wave=parseWaveCsv(waveText);
  const flt=filterSensorFaults(wave,coll);
  wave=flt.wave;
  const lw=wave.length?wave[wave.length-1].t:0;
  const lc=coll.length?coll[coll.length-1].t:0;
  return {coll, wave, fault:flt.fault, lw, lc};
}

if (typeof module==='object' && module.exports){
  module.exports={
    SURF_CM, TIERS, tierOf, HOUR, SURF_VON, SURF_BIS, BIAS_CM, SEE_MAX, SEE_MIN,
    interp, waveFromFab, fabFromTue, lagHours, travel, projProb, dayKey, berlinHour, berlinMin,
    parseTime, parseCollected, parseWaveCsv, filterSensorFaults, buildForecast,
    loadOutlookFromRow, latestOutlook, computeOutlook, prepareData, stamp, fmtTime,
  };
}
