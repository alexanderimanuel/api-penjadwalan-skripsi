import fs from 'node:fs/promises';
import path from 'node:path';
import {Workbook,SpreadsheetFile} from '@oai/artifact-tool';

const folder=path.resolve(process.argv[2]);
const data=JSON.parse(await fs.readFile(path.join(folder,'workbook-data.json'),'utf8'));
const wb=Workbook.create();
const labels=Object.keys(data.metrics);
const palette={header:'#253D61',lesson:'#EFF5FF',break:'#EEF0F3',fixed:'#E8DEF6',conflict:'#FFE0DC',empty:'#FFFFFF'};
const sheet=(name)=>{const s=wb.worksheets.add(name);s.showGridLines=false;return s;};
const base=(s,range)=>{s.getRange(range).format.font={name:'Arial',size:10,color:'#182B43'};s.getRange(range).format.verticalAlignment='center';};
const header=(s,range)=>{s.getRange(range).format={fill:palette.header,font:{name:'Arial',size:10,bold:true,color:'#FFFFFF'},rowHeight:28,horizontalAlignment:'center',verticalAlignment:'center',wrapText:true};};
const title=(s,text)=>{s.getRange('A2').values=[[text]];s.getRange('A2').format.font={name:'Arial',size:15,bold:true,color:palette.header};};
const main=sheet('Perbandingan');main.tabColor=palette.header;
base(main,'A1:E46');main.getRange('A1:A46').format.columnWidth=38;main.getRange('B1:E46').format.columnWidth=20;
main.getRange('A1:E46').format.rowHeight=23;title(main,'SMA Negeri 8 Malang — Perbandingan jadwal');
main.getRange('A4:E4').values=[['Metrik','Baseline','Rekomendasi A','Rekomendasi B','Rekomendasi C']];header(main,'A4:E4');
const metrics=[['H','Total hard violations (H)'],['feasible','Feasible menurut model'],['SC1','SC1 — gap guru (slot KBM)'],['SC2','SC2 — pasangan pertemuan'],['SC3','SC3 — excess HIGH'],['normalized_SC1','SC1 normalized'],['normalized_SC2','SC2 normalized'],['normalized_SC3','SC3 normalized'],['S','Skor soft (S)'],['F','F = 2H + S'],['n_events','Jumlah event'],['jp','JP kelas (bukan JP guru)']];
const metricRows={};
main.getRange('A5:E16').values=metrics.map(([key,label],i)=>{metricRows[key]=i+5;return [label,...labels.map(l=>typeof data.metrics[l][key]==='boolean'?(data.metrics[l][key]?'Ya':'Tidak'):data.metrics[l][key])];});
main.getRange('B5:E16').setNumberFormat('0');main.getRange('B10:E14').setNumberFormat('0.000000');
main.getRange('B13:E14').format.font.bold=true;
main.getRange('A18').values=[['Skor lebih kecil mengikuti model; bukan pilihan otomatis untuk sekolah.']];
main.getRange('A20:E20').values=[['Hard constraint','Baseline','A','B','C']];header(main,'A20:E20');
main.getRange('A21:E26').values=['Guru tidak bentrok','Kelas tidak bentrok','Event lengkap','Slot dan durasi sesuai','Kegiatan tetap terjaga','Penugasan tetap'].map((text,i)=>['HC'+(i+1)+' — '+text,...labels.map(l=>data.metrics[l]['HC'+(i+1)])]);
main.getRange('B21:E26').conditionalFormats.add('cellIs',{operator:'greaterThan',formula:0,format:{fill:palette.conflict,font:{color:'#952A23',bold:true}}});
main.getRange('A29:E29').values=[['Perubahan dari baseline','Baseline','A','B','C']];header(main,'A29:E29');
for (const [i,key] of ['SC1','SC2','SC3','S'].entries()){
  const row=30+i;main.getRange(`A${row}:B${row}`).values=[['Selisih '+key,0]];
  main.getRange(`C${row}:E${row}`).formulas=[['C','D','E'].map(c=>`=${c}${metricRows[key]}-$B$${metricRows[key]}`)];
}
main.getRange('B33:E33').setNumberFormat('0.000000');
main.getRange('A34:B35').values=[['Perubahan S (%)','N/A'],['Perubahan SC2 (%)','N/A']];
main.getRange('C34:E34').formulas=[['C','D','E'].map(c=>`=IF($B$13=0,"N/A",${c}33/$B$13)`)];
main.getRange('C35:E35').formulas=[['C','D','E'].map(c=>`=IF($B$8=0,"N/A",${c}31/$B$8)`)];
main.getRange('C34:E35').setNumberFormat('0.0%');
main.getRange('A38:E38').values=[['Jarak penempatan event','Baseline','A','B','C']];header(main,'A38:E38');
main.getRange('A39:E42').values=labels.map(a=>[a,...labels.map(b=>data.distances[a][b])]);
main.getRange('A44').values=[['Jarak memakai multiset event identik. N/A: denominator baseline nol.']];

const review=sheet('Isian Sekolah');review.tabColor='#B78932';base(review,'A1:E28');
review.getRange('A1:A28').format.columnWidth=26;review.getRange('B1:E28').format.columnWidth=24;review.getRange('A1:E28').format.rowHeight=28;
title(review,'Formulir peninjauan — belum diisi');
review.getRange('A4:A5').values=[['Peran peninjau'],['Kode peninjau']];review.getRange('B4:E5').format.fill='#FFF4CC';
review.getRange('A7:E7').values=[['Jadwal','Kejelasan','Kesesuaian jadwal','Distribusi HIGH','Kegunaan keputusan']];header(review,'A7:E7');review.getRange('A7:E7').format.rowHeight=40;
review.getRange('A8:E11').values=labels.map(l=>[l,null,null,null,null]);review.getRange('B8:E11').format.fill='#FFF4CC';
review.getRange('B8:E11').dataValidation={rule:{type:'list',values:['1','2','3','4','N/A']}};
review.getRange('A13').values=[['Skala 1–4; N/A jika belum dapat menilai. Kosong berarti belum dinilai.']];
review.getRange('A14').values=[['1: sulit/tidak sesuai/tidak membantu. 4: sangat jelas/sesuai/membantu.']];
review.getRange('A16').values=[['Pilihan akhir']];review.getRange('B16').dataValidation={rule:{type:'list',values:['baseline','A','B','C','none']}};review.getRange('B16').format.fill='#FFF4CC';
for (const [row,label] of [[18,'Alasan pilihan'],[22,'Aturan belum dimodelkan'],[26,'Perubahan diperlukan']]){
  review.getRange(`A${row}`).values=[[label]];
  review.getRange(`B${row}:E${row+1}`).format.fill='#FFF4CC';
  review.getRange(`B${row}:E${row+1}`).format.wrapText=true;
  review.getRange(`B${row}:E${row+1}`).format.rowHeight=34;
}

const facts=sheet('Pertimbangan');base(facts,'A1:D8');title(facts,'Perubahan faktual dan trade-off');
facts.getRange('A1:A8').format.columnWidth=14;facts.getRange('B1:C8').format.columnWidth=53;facts.getRange('D1:D8').format.columnWidth=31;
facts.getRange('A4:D4').values=[['Jadwal','Perubahan yang menurunkan penalti','Trade-off dan pihak terdampak','Jarak alternatif']];header(facts,'A4:D4');facts.getRange('A4:D4').format.rowHeight=42;
facts.getRange('A5:D7').values=['A','B','C'].map(l=>{
  const b=data.metrics.BASELINE,q=data.metrics[l],dist=data.teacher_distribution[l];
  const improved=['H','SC1','SC2','SC3','S'].filter(k=>q[k]<b[k]).map(k=>`${k}: ${b[k]} menjadi ${q[k]}`).join('\n');
  const worsened=['SC1','SC2','SC3'].filter(k=>q[k]>b[k]).map(k=>`${k}: ${b[k]} menjadi ${q[k]}`).join('\n');
  return [l,improved,worsened+`\nGap guru: ${dist.DECREASED} berkurang, ${dist.UNCHANGED} tetap, ${dist.INCREASED} bertambah.`,['A','B','C'].filter(k=>k!==l).map(k=>`${l}–${k}: ${data.distances[l][k]} event`).join('\n')];
});facts.getRange('A5:D7').format.wrapText=true;facts.getRange('A5:D7').format.rowHeight=112;

const previewRanges=[];
for (const label of labels){
  for (const kind of ['kelas','guru']){
    const name=(label==='BASELINE'?'BL':label)+' '+(kind==='kelas'?'Kelas':'Guru');const s=sheet(name);
    const grids=data.grids[label][kind];let row=5;
    const total=5+grids.reduce((n,g)=>n+g.rows.length+4,0);
    base(s,`A1:F${total}`);s.getRange(`A1:A${total}`).format.columnWidth=10;s.getRange(`B1:F${total}`).format.columnWidth=27;
    title(s,`${label} — Jadwal ${kind}`);
    s.getRange('A3').values=[['Sel mencantumkan JP dan jam. Abu: istirahat; ungu: kegiatan tetap; merah: bentrok.']];
    s.freezePanes.freezeRows(3);
    for (const g of grids){
      s.getRange(`A${row}`).values=[[g.resource_id]];s.getRange(`A${row}`).format.font.bold=true;s.getRange(`A${row}`).format.rowHeight=27;
      s.getRange(`A${row+1}:F${row+1}`).values=[g.headers];header(s,`A${row+1}:F${row+1}`);
      const top=row+2,bottom=top+g.rows.length-1;
      s.getRange(`A${top}:F${bottom}`).values=g.rows.map(r=>r.map(c=>c.text));
      s.getRange(`A${top}:F${bottom}`).format={fill:palette.lesson,wrapText:true,rowHeight:62,verticalAlignment:'center'};
      s.getRange(`A${top}:A${bottom}`).format.fill='#FFFFFF';
      for(let r=0;r<g.rows.length;r++)for(let c=1;c<6;c++){
        const state=g.rows[r][c].state;
        if(state!=='lesson')s.getCell(top-1+r,c).format.fill=palette[state]||'#FFFFFF';
      }
      if((label==='BASELINE'&&kind==='guru'&&g.resource_id==='G039')||(label==='A'&&kind==='kelas'&&g.resource_id==='X-1'))previewRanges.push({sheet:name,range:`A${row}:F${bottom}`,tag:label+'-'+kind});
      row=bottom+3;
    }
  }
}
const meta=sheet('Metadata');base(meta,'A1:F45');title(meta,'Sumber, versi dan parameter penelitian');meta.getRange('A1:A45').format.columnWidth=29;meta.getRange('B1:B45').format.columnWidth=94;meta.getRange('A1:B45').format.rowHeight=28;
const info=[['Dataset version',data.metadata.dataset_version],['Source snapshot',data.metadata.source_snapshot_version],['Dataset hash',data.metadata.dataset_hash],['Rules version',data.metadata.rules_version],['Rules hash',data.metadata.rules_hash],['Difficulty version',data.metadata.difficulty_version],['Difficulty source',data.metadata.difficulty_source],['Bobot SC1/SC2/SC3',JSON.stringify(data.metadata.weights)],['HIGH daily limit',data.metadata.daily_limit],['Normalization',JSON.stringify(data.metadata.normalization.upper_bounds)],['Method version',data.metadata.method_version],['Identitas guru',data.metadata.teacher_identifiers],['Status metode',data.metadata.scope],['Asumsi penelitian',data.metadata.assumptions],['Makna peringkat',data.metadata.ranking]];
meta.getRange('A4:B18').values=info;meta.getRange('B4:B18').format.wrapText=true;meta.getRange('A10:B10').format.rowHeight=48;meta.getRange('A16:B18').format.rowHeight=45;
meta.getRange('A21:F21').values=[['Jadwal','Run ID','Seed','Alpha / L','Evaluasi','Stop']];header(meta,'A21:F21');
meta.getRange('B21:B25').format.columnWidth=70;meta.getRange('C21:F25').format.columnWidth=19;
meta.getRange('A22:F25').values=labels.map(l=>{const r=data.metadata.source_runs[l];return [l,r.run_id||'Jadwal sumber 3 Agustus 2026',r.seed??'N/A',r.config?`${r.config.alpha} / ${r.config.L}`:'N/A',r.evaluations??'N/A',r.stop_reason||'N/A'];});

wb.recalculate();
const check=await wb.inspect({kind:'table',range:'Perbandingan!A4:E16',include:'values,formulas',tableMaxRows:14,tableMaxCols:5,maxChars:4000});
const errors=await wb.inspect({kind:'match',searchTerm:'#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!',options:{useRegex:true,maxResults:50},maxChars:3000});
const verification={main:check.ndjson,formulaErrors:errors.ndjson,percentZeroBaseline:main.getRange('C35:E35').values,reviewRatings:review.getRange('B8:E11').values};
if(verification.percentZeroBaseline[0].some(v=>v!=='N/A'))throw new Error('Zero baseline percent is not N/A');
if(verification.reviewRatings.flat().some(v=>v!==null&&v!==''&&v!==undefined))throw new Error('Reviewer answers must remain blank');
await fs.mkdir(path.join(folder,'verification'),{recursive:true});
await fs.writeFile(path.join(folder,'verification','workbook-check.json'),JSON.stringify(verification,null,2));
for(const item of [{sheet:'Perbandingan',range:'A1:E44',tag:'comparison'},{sheet:'Isian Sekolah',range:'A1:E28',tag:'review'},{sheet:'Pertimbangan',range:'A1:D8',tag:'tradeoffs'},{sheet:'Metadata',range:'A1:B18',tag:'metadata'},...previewRanges]){
  const preview=await wb.render({sheetName:item.sheet,range:item.range,scale:1,format:'png'});
  await fs.writeFile(path.join(folder,'verification',item.tag+'.png'),new Uint8Array(await preview.arrayBuffer()));
}
const file=await SpreadsheetFile.exportXlsx(wb);await file.save(path.join(folder,'Review-SMAN8.xlsx'));
console.log('Workbook exported; calculated comparison, blank reviewer fields and semantic schedule styles verified.');
