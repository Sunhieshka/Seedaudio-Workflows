// Short Drama Pipeline — one page, seven steps, one project saved on the server.
// Script → prompts → character sheets / location sheets → asset library →
// shots (Seedance, references mapped from the library) → combine → Seed Audio redub.
const { useState, useEffect, useRef, useMemo } = React;

// ---- helpers --------------------------------------------------------------------

const uid = () => Math.random().toString(36).slice(2, 10);
const clone = (x) => JSON.parse(JSON.stringify(x));

async function api(path, body) {
  const res = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
  let data = {};
  try { data = await res.json(); } catch (e) { /* empty body */ }
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

function readFile(file) {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(r.result);
    r.onerror = () => reject(r.error);
    r.readAsDataURL(file);
  });
}

async function uploadFile(file, hint) {
  return (await api("/api/upload", { data_url: await readFile(file), hint })).url;
}

const fmtTime = (sec) => `${Math.floor(sec / 60)}:${String(Math.round(sec % 60)).padStart(2, "0")}`;
const shotLetter = (i) => (i < 26 ? String.fromCharCode(65 + i) : String(i + 1));

// Preset Seed Audio voices, sent as "speaker://<id>".
const VOICE_PRESETS = [
  { id: "en_female_wenrouzhishijieshuonv_uranus_bigtts", name: "Megan", about: "Gentle, fun older sister · American" },
  { id: "en_male_valentino_corey_uranus_bigtts", name: "Clark", about: "Mature, dignified uncle · American" },
  { id: "en_male_valentino_uranus_bigtts", name: "Valentino", about: "Sunny, cheerful young man" },
  { id: "en_female_stokie_uranus_bigtts", name: "Stokie", about: "Trendy, casual, expressive young female" },
  { id: "en_female_dacey_uranus_bigtts", name: "Dacey", about: "Crisp, confident young female" },
  { id: "en_male_tim_uranus_bigtts", name: "Tim", about: "Clear, friendly mid-range male" },
  { id: "en_female_nadia_uranus_bigtts", name: "Blair", about: "Clean, gentle young female" },
  { id: "en_male_kevin_uranus_bigtts", name: "Kevin", about: "Young, clear, articulate male" },
  { id: "en_male_jamie_uranus_bigtts", name: "Jamie", about: "Hearty, witty, energetic male" },
  { id: "en_female_hayley_uranus_bigtts", name: "Hayley", about: "Lively, expressive storyteller" },
];

// The redub pre-prompt. [language] is filled in; the shot prompts are appended in order.
const DUB_TEMPLATE = `The provided video contains some [language] pronunciation errors. Redub the video using the exact same dialogue, correcting all mispronunciations.
Keep the original characters’ primary voice qualities intact, including their voice texture, pitch, tone, accent, and speaking style. Preserve the distinction between [both] characters’ voices. Match the original dialogue, timing, emotions, and lip movements as closely as possible.
Do not change the dialogue, visuals, character actions, or background ambience. Only correct the spoken audio while maintaining the original audio quality and natural conversational delivery.`;

function voiceRef(v) {
  if (!v) return "";
  if (v.kind === "preset" && v.preset) return "speaker://" + v.preset;
  if (v.kind === "custom" && (v.custom || "").trim()) return "speaker://" + v.custom.trim().replace(/^speaker:\/\//, "");
  if (v.kind === "clip" && v.clip) return v.clip;
  return "";
}

// Characters with a mapped voice, in cast order; @Audio numbers follow this order.
function mappedVoices(project) {
  return project.characters
    .map((c) => ({ name: c.name, ref: voiceRef(project.dub.voices[c.id]) }))
    .filter((v) => v.ref);
}

// The redub header: pre-prompt (language filled in) and one "NAME’s voice must match @AudioN." line per
// mapped voice. The server appends each shot's prompt (CINEMATOGRAPHY removed, beats in video time).
function dubHeader(project) {
  const n = project.characters.length;
  let text = DUB_TEMPLATE
    .replace("[language]", (project.dub.language || "").trim() || "[language]")
    .replace("[both] characters’", n === 2 ? "both characters’" : "all characters’");
  const voices = mappedVoices(project);
  if (voices.length) text += "\n" + voices.map((v, i) => `${v.name}’s voice must match @Audio${i + 1}.`).join(" ");
  return text;
}

// ---- dub parts ----
// Long episodes are redubbed in parts and joined. A part ends only at a shot boundary, runs about
// 3 minutes at most (Seed Audio's limit is 6; shorter takes keep timing and lip sync tight), and holds
// shot text the Seed Audio 1.5 skill can fit under 10,000 characters (~30k before optimizing). When a
// part must close, it closes at a location change in its back half if there is one, so the seam lands
// on a scene cut where the ambience changes anyway.
const PART_MAX_SECONDS = 180;
const PART_MAX_CHARS = 30000;
const shotTextLength = (s) => s.prompt.replace(/(\*\*)?CINEMATOGRAPHY:(\*\*)?[\s\S]*?(?=(\*\*)?AUDIO:|$)/i, "").length;

function planParts(project) {
  const shots = project.shots.filter((s) => s.include !== false);
  const kinds = Object.fromEntries(project.library.items.map((it) => [it.id, it.kind]));
  const place = (s) => (s.refIds || []).filter((id) => kinds[id] === "location").sort().join(",")
    || ((s.prompt.match(/IN THIS SHOT:([^\n]*)/i) || [])[1] || "").split(/[,;]/).slice(-1)[0];
  const parts = [];
  let cur = [];
  const size = (xs) => [xs.reduce((a, x) => a + (Number(x.seconds) || 0), 0), xs.reduce((a, x) => a + shotTextLength(x), 0)];
  for (const s of shots) {
    const [sec, chars] = size(cur);
    if (cur.length && (sec + (Number(s.seconds) || 0) > PART_MAX_SECONDS || chars + shotTextLength(s) > PART_MAX_CHARS)) {
      let cut = cur.length;
      for (let k = cur.length - 1; k >= Math.ceil(cur.length / 2); k--) {
        if (place(cur[k]) !== place(cur[k - 1])) { cut = k; break; }
      }
      parts.push(cur.slice(0, cut));
      cur = cur.slice(cut);
    }
    cur.push(s);
  }
  if (cur.length) parts.push(cur);
  return parts.map((xs) => newPart(xs.map((x) => x.id)));
}

const newPart = (shotIds) => ({ id: uid(), shotIds, combined: null, prompt: "", edited: false, job: null, result: null });
const partShots = (project, part) => part.shotIds.map((id) => project.shots.find((s) => s.id === id)).filter(Boolean);
const partLabel = (project, part) => {
  const ls = part.shotIds.map((id) => project.shots.findIndex((s) => s.id === id)).filter((i) => i >= 0).map(shotLetter);
  return ls.length > 2 ? `${ls[0]}–${ls[ls.length - 1]}` : ls.join(", ");
};

// Older projects redubbed one combined video; show that as a single part.
function migrateDub(p) {
  if (!p || p.dub.parts) return p;
  p.dub.parts = [];
  if (p.dub.result && p.combine.url) {
    p.dub.parts.push({ ...newPart(p.combine.shots || []), combined: { url: p.combine.url, duration: p.combine.duration, spans: p.combine.spans },
      prompt: p.dub.sentPrompt || p.dub.prompt || "", edited: true, result: p.dub.result });
    p.dub.final = { url: p.dub.result.final, parts: [p.dub.parts[0].id] };
  }
  delete p.dub.prompt; delete p.dub.edited; delete p.dub.optimized; delete p.dub.optJob; delete p.dub.job;
  delete p.dub.missingLines; delete p.dub.result; delete p.dub.sentPrompt;
  return p;
}

function selectedTake(shot) {
  const t = (shot.takes || [])[shot.selected];
  return t && t.video ? t : null;
}

// Who / what a library item is, for auto-map: a character's role and look, a location's scenes and description.
function libraryContext(project, it) {
  const subj = (it.subject || it.name || "").toLowerCase();
  if (it.kind === "character") {
    const c = project.characters.find((x) => x.name.toLowerCase() === subj);
    return c ? [c.role, c.description].filter(Boolean).join(" — ").slice(0, 400) : "";
  }
  if (it.kind === "location") {
    const l = project.locations.find((x) => x.name.toLowerCase() === subj);
    return l ? [l.scenes && `scenes: ${l.scenes}`, l.direction].filter(Boolean).join(" — ").slice(0, 400) : "";
  }
  return "";
}

// What a library item is called in a shot prompt: "@Image2 is <this>".
function bindName(item) {
  if (item.kind === "character") return item.subject || item.name;
  if (item.kind === "location") return `the location ${item.subject || item.name}${item.label ? ` (${item.label})` : ""}`;
  return item.name;
}

function newProject(name) {
  return {
    id: uid(), name: name || "Untitled drama", created: Date.now(),
    script: { text: "", pdfName: "", job: null },
    characters: [], locations: [], shots: [],
    library: { group_id: "", items: [] },
    settings: { model: "dreamina-seedance-2-5-260628", resolution: "720p", ratio: "16:9" },
    combine: { url: "", status: "", error: "" },
    dub: { language: "English", voices: {}, format: "wav", sampleRate: 44100, optimize: "auto", parts: [], final: null },
  };
}

const running = (j) => j && (j.status === "running" || j.status === "queued");

// ---- small components ---------------------------------------------------------

function Status({ job }) {
  if (!job) return null;
  if (running(job)) return <span className="pill run"><span className="spinner" /> {job.stage || "Running"}</span>;
  if (job.status === "done" || job.status === "succeeded") return <span className="pill ok">Done</span>;
  if (job.status === "failed") return <span className="pill bad">Failed</span>;
  return <span className="pill">{job.status}</span>;
}

function Lightbox({ src, onClose }) {
  if (!src) return null;
  return <div className="lightbox" onClick={onClose}><img src={src} /></div>;
}

function FileButton({ label, accept, onFile, disabled, multiple, className }) {
  const ref = useRef(null);
  return (
    <>
      <button className={className || "b sm"} disabled={disabled} onClick={() => ref.current.click()}>{label}</button>
      <input ref={ref} type="file" accept={accept} multiple={multiple} style={{ display: "none" }}
        onChange={(e) => { const files = [...e.target.files]; e.target.value = ""; if (files.length) onFile(multiple ? files : files[0]); }} />
    </>
  );
}

// ---- step 1: script -------------------------------------------------------------

function ScriptStep({ project, update, startJob }) {
  const [pdf, setPdf] = useState(null);
  const job = project.script.job;
  const hasOutput = project.shots.length || project.characters.length || project.locations.length;

  async function run() {
    if (hasOutput && !confirm("Replace the current shots, characters and locations with a new breakdown?")) return;
    const body = { script_text: project.script.text };
    if (pdf) body.pdf_data_url = await readFile(pdf);
    try {
      const { job_id } = await api("/api/script", { ...body, project: project.name });
      update((p) => { p.script.job = { job_id, status: "running", stage: "Starting" }; p.script.pdfName = pdf ? pdf.name : ""; });
    } catch (e) { update((p) => { p.script.job = { status: "failed", error: e.message }; }); }
  }

  return (
    <>
      <p className="sub">Seed LLM splits the script into shots of up to 30 s and writes one Seedance prompt per shot, plus a character sheet description per character and a location direction per location.</p>
      <div className="card">
        <label className="f">Script</label>
        <textarea className="tall" value={project.script.text} placeholder="Paste the script / screenplay here…"
          onChange={(e) => { const v = e.target.value; update((p) => { p.script.text = v; }); }} />
        <div className="row" style={{ marginTop: 10 }}>
          <FileButton label={pdf ? `PDF: ${pdf.name}` : "Upload PDF"} accept="application/pdf" onFile={setPdf} />
          {pdf && <button className="b sm" onClick={() => setPdf(null)}>Remove PDF</button>}
          <div className="grow" />
          <Status job={job} />
          <button className="b primary" disabled={running(job) || (!project.script.text.trim() && !pdf)} onClick={run}>Generate prompts</button>
        </div>
        {job && job.status === "failed" && <div className="err">{job.error}</div>}
      </div>
      {hasOutput ? (
        <div className="card">
          <h3>Breakdown</h3>
          <div className="note">{project.shots.length} shots · {project.characters.length} characters · {project.locations.length} locations — edit them in the next steps.</div>
          <div className="thumbs" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(320px, 1fr))" }}>
            {project.shots.map((s, i) => (
              <div key={s.id} className="take"><b>Shot {shotLetter(i)}</b> <span className="note">· {s.seconds}s</span>
                <pre className="p" style={{ maxHeight: 160 }}>{s.prompt}</pre></div>
            ))}
          </div>
        </div>
      ) : null}
    </>
  );
}

// ---- step 2: characters ---------------------------------------------------------

function CharactersStep({ project, update, addToLibrary, zoom, uploadAllButton }) {
  async function generate(c) {
    try {
      const { job_id } = await api("/api/sheet/start", { notes: c.description, images: c.refs || [], project: project.name, name: c.name });
      update((p) => { const x = p.characters.find((y) => y.id === c.id); x.sheet = { job_id, status: "running", stage: "Starting", kind: "sheet" }; });
    } catch (e) { update((p) => { p.characters.find((y) => y.id === c.id).sheet = { status: "failed", error: e.message }; }); }
  }
  async function inspect(c) {
    try {
      await api("/api/sheet/inspect", { job_id: c.sheet.job_id });
      update((p) => { const s = p.characters.find((y) => y.id === c.id).sheet; s.status = "running"; s.stage = "Inspecting"; });
    } catch (e) { alert(e.message); }
  }
  const set = (id, fn) => update((p) => fn(p.characters.find((y) => y.id === id)));
  const pending = project.characters.filter((c) => !running(c.sheet) && !(c.sheet && c.sheet.image));

  return (
    <>
      <p className="sub">One 16:9 reference sheet per character (face close-up, front, side, back) from the description and any reference photos. Add the ones you want to use to the asset library.</p>
      <div className="row" style={{ marginBottom: 14 }}>
        <button className="b" onClick={() => update((p) => { p.characters.push({ id: uid(), name: "New character", role: "", description: "", refs: [], sheet: null }); })}>+ Add character</button>
        <button className="b primary" disabled={!pending.length} onClick={() => pending.forEach(generate)}>Generate all missing sheets ({pending.length})</button>
        {uploadAllButton}
      </div>
      {project.characters.map((c) => {
        const sheet = c.sheet;
        const inLib = project.library.items.find((it) => it.image === (sheet && sheet.image));
        return (
          <div className="card" key={c.id}>
            <div className="cols">
              <div>
                <div className="row">
                  <input type="text" className="grow" value={c.name} onChange={(e) => { const v = e.target.value; set(c.id, (x) => { x.name = v; }); }} style={{ fontWeight: 600 }} />
                  <button className="b sm danger" onClick={() => confirm(`Remove ${c.name}?`) && update((p) => { p.characters = p.characters.filter((y) => y.id !== c.id); })}>Remove</button>
                </div>
                {c.role && <div className="note" style={{ marginTop: 6 }}>{c.role}</div>}
                <label className="f">Sheet description</label>
                <textarea value={c.description} onChange={(e) => { const v = e.target.value; set(c.id, (x) => { x.description = v; }); }} style={{ minHeight: 150 }} />
                <label className="f">Reference photos (optional — face, outfit)</label>
                <div className="row">
                  {(c.refs || []).map((u) => (
                    <div key={u} style={{ position: "relative" }}>
                      <img src={u} style={{ width: 64, height: 64, objectFit: "cover", borderRadius: 6 }} />
                      <button className="b sm" style={{ position: "absolute", top: -6, right: -6, padding: "0 5px" }} onClick={() => set(c.id, (x) => { x.refs = x.refs.filter((r) => r !== u); })}>×</button>
                    </div>
                  ))}
                  <FileButton label="+ Photo" accept="image/*" multiple onFile={async (files) => {
                    const urls = await Promise.all(files.map((f) => uploadFile(f, c.name)));
                    set(c.id, (x) => { x.refs = [...(x.refs || []), ...urls]; });
                  }} />
                </div>
                <div className="row" style={{ marginTop: 12 }}>
                  <button className="b primary" disabled={running(sheet)} onClick={() => generate(c)}>{sheet && sheet.image ? "Regenerate sheet" : "Generate sheet"}</button>
                  {sheet && sheet.image && sheet.job_id && !running(sheet) && <button className="b" onClick={() => inspect(c)}>QA check & retry</button>}
                  <Status job={sheet} />
                </div>
                {sheet && sheet.status === "failed" && <div className="err">{sheet.error}</div>}
              </div>
              <div>
                {sheet && sheet.image ? (
                  <>
                    <img src={sheet.image} onClick={() => zoom(sheet.image)} style={{ width: "100%", borderRadius: 8, cursor: "zoom-in" }} />
                    {sheet.verdict && <div className="note" style={{ marginTop: 6 }}>{sheet.verdict}</div>}
                    <div className="row" style={{ marginTop: 8 }}>
                      {inLib ? <LibState item={inLib} /> : (
                        <button className="b" onClick={() => addToLibrary({ name: `${c.name} — character sheet`, subject: c.name, kind: "character", image: sheet.image })}>Add to asset library</button>
                      )}
                      <a className="b sm" href={sheet.image} download style={{ textDecoration: "none" }}>Download</a>
                    </div>
                  </>
                ) : <div className="empty">No sheet yet</div>}
              </div>
            </div>
          </div>
        );
      })}
      {!project.characters.length && <div className="card note">No characters yet — run Script to Prompt, or add one.</div>}
    </>
  );
}

function LibState({ item }) {
  if (item.status === "uploading") return <span className="pill run"><span className="spinner" /> Uploading to library</span>;
  if (item.status === "failed") return <span className="pill bad" title={item.error}>Upload failed</span>;
  return <span className="pill ok">In asset library</span>;
}

// ---- step 3: locations ----------------------------------------------------------

function LocationsStep({ project, update, addToLibrary, zoom, uploadAllButton }) {
  async function generate(l) {
    try {
      const { job_id } = await api("/api/location/start", { direction: l.direction, variations: l.variations, project: project.name, name: l.name });
      update((p) => { p.locations.find((y) => y.id === l.id).job = { job_id, status: "running", stage: "Starting" }; });
    } catch (e) { update((p) => { p.locations.find((y) => y.id === l.id).job = { status: "failed", error: e.message }; }); }
  }
  const set = (id, fn) => update((p) => fn(p.locations.find((y) => y.id === id)));
  const pending = project.locations.filter((l) => !running(l.job) && !l.plate);

  return (
    <>
      <p className="sub">An empty plate of each location, plus coverage / time-of-day variations edited from the same plate so the architecture stays fixed.</p>
      <div className="row" style={{ marginBottom: 14 }}>
        <button className="b" onClick={() => update((p) => { p.locations.push({ id: uid(), name: "NEW LOCATION", scenes: "", direction: "", variations: 3, job: null, plate: "", images: [] }); })}>+ Add location</button>
        <button className="b primary" disabled={!pending.length} onClick={() => pending.forEach(generate)}>Generate all missing ({pending.length})</button>
        {uploadAllButton}
      </div>
      {project.locations.map((l) => {
        const imgs = l.plate ? [{ label: "Plate", url: l.plate }, ...(l.images || [])] : [];
        return (
          <div className="card" key={l.id}>
            <div className="row">
              <input type="text" className="grow" value={l.name} onChange={(e) => { const v = e.target.value; set(l.id, (x) => { x.name = v; }); }} style={{ fontWeight: 600 }} />
              <button className="b sm danger" onClick={() => confirm(`Remove ${l.name}?`) && update((p) => { p.locations = p.locations.filter((y) => y.id !== l.id); })}>Remove</button>
            </div>
            {l.scenes && <div className="note" style={{ marginTop: 6 }}>Used in: {l.scenes}</div>}
            <label className="f">Direction (the place, then "Explore:" variations)</label>
            <textarea value={l.direction} onChange={(e) => { const v = e.target.value; set(l.id, (x) => { x.direction = v; }); }} />
            <div className="row" style={{ marginTop: 10 }}>
              <span className="note">Variations</span>
              <select value={l.variations} onChange={(e) => { const v = Number(e.target.value); set(l.id, (x) => { x.variations = v; }); }}>
                {[0, 1, 2, 3, 4, 5, 6].map((n) => <option key={n} value={n}>{n}</option>)}
              </select>
              <button className="b primary" disabled={running(l.job) || !l.direction.trim()} onClick={() => generate(l)}>{l.plate ? "Regenerate" : "Generate"}</button>
              <Status job={l.job} />
            </div>
            {l.job && l.job.status === "failed" && <div className="err">{l.job.error}</div>}
            {imgs.length > 0 && (
              <div className="thumbs">
                {imgs.map((im) => {
                  const inLib = project.library.items.find((it) => it.image === im.url);
                  return (
                    <div className="thumb" key={im.url}>
                      <img src={im.url} onClick={() => zoom(im.url)} />
                      <div className="cap">
                        <b>{im.label}</b>
                        {im.prompt && <span className="note">{im.prompt}</span>}
                        {inLib ? <LibState item={inLib} /> : (
                          <button className="b sm" onClick={() => addToLibrary({ name: `${l.name} — ${im.label}`, subject: l.name, label: im.label === "Plate" ? "" : im.label, kind: "location", image: im.url })}>Add to asset library</button>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        );
      })}
      {!project.locations.length && <div className="card note">No locations yet — run Script to Prompt, or add one.</div>}
    </>
  );
}

// ---- step 4: asset library ------------------------------------------------------

function LibraryStep({ project, update, addToLibrary, retryLibrary, zoom, uploadAllButton }) {
  const [name, setName] = useState("");
  const items = project.library.items;
  return (
    <>
      <p className="sub">Images uploaded to this project's ModelArk asset group. Only library assets can be mapped to shots — Seedance reads them as <code>asset://</code> references.</p>
      <div className="card">
        <div className="row">
          <input type="text" className="grow" placeholder="Name for an uploaded image (e.g. Mara's necklace)" value={name} onChange={(e) => setName(e.target.value)} />
          <FileButton className="b primary" label="Upload image to library" accept="image/*" disabled={!name.trim()} onFile={async (f) => {
            const url = await uploadFile(f, name);
            addToLibrary({ name: name.trim(), subject: name.trim(), kind: "other", image: url });
            setName("");
          }} />
        </div>
        <div className="row" style={{ marginTop: 10 }}>
          {uploadAllButton}
          <span className="note">Adds every generated character sheet and location image not in the library yet, and retries failed uploads.</span>
        </div>
        <div className="note" style={{ marginTop: 8 }}>Asset group: {project.library.group_id || "created on the first upload"}</div>
      </div>
      <div className="thumbs">
        {items.map((it) => (
          <div className="thumb" key={it.id}>
            <img src={it.image} onClick={() => zoom(it.image)} />
            <div className="cap">
              <b>{it.name}</b>
              <span className="note">{it.kind} · binds as “{bindName(it)}”</span>
              <div className="row">
                <LibState item={it} />
                {it.status === "failed" && <button className="b sm" onClick={() => retryLibrary(it)}>Retry</button>}
                <button className="b sm danger" onClick={() => update((p) => {
                  p.library.items = p.library.items.filter((x) => x.id !== it.id);
                  p.shots.forEach((s) => { s.refIds = (s.refIds || []).filter((r) => r !== it.id); });
                })}>Remove</button>
              </div>
              {it.status === "failed" && <div className="err">{it.error}</div>}
              {it.asset_id && <span className="note" style={{ wordBreak: "break-all" }}>{it.asset_id}</span>}
            </div>
          </div>
        ))}
      </div>
      {!items.length && <div className="card note">The library is empty — add character sheets and location plates from steps 2 and 3.</div>}
    </>
  );
}

// ---- step 5: shots --------------------------------------------------------------

function ShotsStep({ project, update, config }) {
  const ready = project.library.items.filter((it) => it.status === "ready");
  const models = (config && config.video_models) || [];
  const model = models.find((m) => m.id === project.settings.model) || models[0];
  const set = (id, fn) => update((p) => fn(p.shots.find((y) => y.id === id)));

  // The server matches "IN THIS SHOT:" entries to library characters and locations (pipeline/steps.py auto_map).
  const [mapping, setMapping] = useState(false);
  async function autoMap(shots) {
    setMapping(true);
    try {
      const { maps } = await api("/api/shot/automap", {
        shots: shots.map((s) => ({ id: s.id, prompt: s.prompt })),
        items: ready.map((it) => ({ id: it.id, name: it.name, subject: it.subject, kind: it.kind, label: it.label, context: libraryContext(project, it) })),
      });
      update((p) => { p.shots.forEach((x) => { if (maps[x.id]) x.refIds = maps[x.id]; }); });
    } catch (e) { alert(e.message); }
    setMapping(false);
  }

  async function generate(s, index) {
    const prev = index > 0 ? project.shots[index - 1] : null;
    const startFrame = s.chain && prev && selectedTake(prev) ? selectedTake(prev).last_frame : "";
    if (s.chain && !startFrame) { alert("The previous shot has no finished video with a last frame yet."); return; }
    if (selectedTake(s) && !confirm(`Regenerate shot ${shotLetter(index)}? Its current video will be replaced.`)) return;
    const refs = (s.refIds || []).map((id) => ready.find((it) => it.id === id)).filter(Boolean)
      .map((it) => ({ asset_id: it.asset_id, name: bindName(it), image: it.image }));
    const optimize = project.settings.optimize !== false;
    set(s.id, (x) => { x.job = { status: "running", stage: optimize ? "Optimizing with SD 2.5 skill" : "Submitting" }; });
    try {
      const r = await api("/api/shot/start", {
        prompt: s.prompt, refs, start_frame: startFrame, model: project.settings.model, resolution: project.settings.resolution,
        ratio: project.settings.ratio, duration: s.seconds, takes: 1, optimize, project: project.name, shot: shotLetter(index),
      });
      // One video per shot: a new generation replaces the old one.
      set(s.id, (x) => {
        x.sentPrompt = r.prompt;
        x.optimizedPrompt = r.optimized || "";
        x.job = null;
        x.takes = r.task_ids.slice(0, 1).map((t) => ({ task_id: t, status: "queued" }));
        x.selected = 0;
      });
    } catch (e) { set(s.id, (x) => { x.job = { status: "failed", error: e.message }; }); }
  }

  const idle = project.shots.filter((s, i) => !running((s.takes || [])[s.selected]) && !running(s.job) && !selectedTake(s) && !(s.chain && i > 0));

  return (
    <>
      <p className="sub">Each shot is one Seedance video. On Generate, the shot prompt from Script to Prompt is optimized with the Seedance 2.5 skill (Seed LLM sees the mapped reference images), then sent to Seedance. Each library reference becomes <code>@ImageN is NAME</code> at the top of the prompt. Chained shots open on the previous shot's last frame.</p>
      <div className="card">
        <div className="row">
          <span className="note">Model</span>
          <select value={project.settings.model} onChange={(e) => { const v = e.target.value; update((p) => { p.settings.model = v; }); }}>
            {models.map((m) => <option key={m.id} value={m.id}>Seedance {m.label} (≤{m.max_duration}s)</option>)}
          </select>
          <span className="note">Resolution</span>
          <select value={project.settings.resolution} onChange={(e) => { const v = e.target.value; update((p) => { p.settings.resolution = v; }); }}>
            {(model ? model.resolutions : ["720p"]).map((r) => <option key={r}>{r}</option>)}
          </select>
          <span className="note">Ratio</span>
          <select value={project.settings.ratio} onChange={(e) => { const v = e.target.value; update((p) => { p.settings.ratio = v; }); }}>
            {["16:9", "9:16", "1:1", "4:3", "3:4", "21:9"].map((r) => <option key={r}>{r}</option>)}
          </select>
          <label className="row note" style={{ gap: 6, cursor: "pointer" }} title="Seed LLM rewrites each shot prompt with the Seedance 2.5 skill (skills/sd25-pe) before it goes to Seedance">
            <input type="checkbox" checked={project.settings.optimize !== false} onChange={(e) => { const v = e.target.checked; update((p) => { p.settings.optimize = v; }); }} />
            Optimize with SD 2.5 skill
          </label>
          <div className="grow" />
          <button className="b" onClick={() => autoMap(project.shots)} disabled={!ready.length || mapping}>{mapping ? <><span className="spinner" /> Mapping…</> : "Auto-map all shots"}</button>
          <button className="b primary" disabled={!idle.length} onClick={() => idle.forEach((s) => generate(s, project.shots.indexOf(s)))}>Generate unchained shots ({idle.length})</button>
        </div>
        {!ready.length && <div className="note" style={{ marginTop: 8 }}>No assets ready in the library yet — shots will be text-only until you add some.</div>}
      </div>

      {project.shots.map((s, i) => {
        const take = (s.takes || [])[s.selected] || null;
        const busy = running(s.job) || running(take);
        const order = s.refIds || [];
        const offset = s.chain && i > 0 ? 2 : 1;
        return (
          <div className="card" key={s.id}>
            <h3>Shot {shotLetter(i)}
              <span className="note">duration</span>
              <input type="number" min="4" max={model ? model.max_duration : 30} value={s.seconds} style={{ width: 70 }}
                onChange={(e) => { const v = Number(e.target.value); set(s.id, (x) => { x.seconds = v; }); }} />
              <span className="note">s</span>
              <div className="grow" />
              <button className="b sm danger" onClick={() => confirm(`Delete shot ${shotLetter(i)}?`) && update((p) => { p.shots = p.shots.filter((y) => y.id !== s.id); })}>Delete</button>
            </h3>
            <textarea value={s.prompt} style={{ minHeight: 180 }} onChange={(e) => { const v = e.target.value; set(s.id, (x) => { x.prompt = v; }); }} />
            <div className="row" style={{ marginTop: 10 }}>
              <b style={{ fontSize: 13 }}>References</b>
              <span className="note">click to map, in order</span>
              <button className="b sm" disabled={!ready.length || mapping} onClick={() => autoMap([s])}>Auto-map this shot</button>
              {order.length > 0 && <button className="b sm" onClick={() => set(s.id, (x) => { x.refIds = []; })}>Clear</button>}
              {i > 0 && (
                <label className="row note" style={{ gap: 6, cursor: "pointer" }}>
                  <input type="checkbox" checked={!!s.chain} onChange={(e) => { const v = e.target.checked; set(s.id, (x) => { x.chain = v; }); }} />
                  Start from shot {shotLetter(i - 1)}'s last frame
                </label>
              )}
            </div>
            {ready.length > 0 && (
              <div className="thumbs" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(130px, 1fr))" }}>
                {ready.map((it) => {
                  const pos = order.indexOf(it.id);
                  return (
                    <div key={it.id} className={"thumb pick" + (pos >= 0 ? " sel" : "")}
                      onClick={() => set(s.id, (x) => { x.refIds = pos >= 0 ? order.filter((r) => r !== it.id) : [...order, it.id]; })}>
                      {pos >= 0 && <span className="badge">@Image{pos + offset}</span>}
                      <img src={it.image} />
                      <div className="cap"><span>{it.name}</span></div>
                    </div>
                  );
                })}
              </div>
            )}
            <div className="row" style={{ marginTop: 12 }}>
              <button className="b primary" disabled={busy || !s.prompt.trim()} onClick={() => generate(s, i)}>{take ? "Regenerate" : "Generate"}</button>
              <Status job={s.job} />
              {s.sentPrompt && <details className="grow"><summary>Prompt sent to Seedance{s.optimizedPrompt ? " (SD 2.5 optimized)" : ""}</summary><pre className="p">{s.sentPrompt}</pre></details>}
            </div>
            {s.job && s.job.status === "failed" && <div className="err">{s.job.error}</div>}
            {take && (
              <div className="shot-video">
                {take.video ? <video src={take.video} controls preload="metadata" /> : (
                  <div className="note video-pending">
                    {running(take) ? <span><span className="spinner" /> Seedance {take.status}…</span> : take.status}
                  </div>
                )}
                {take.error && <div className="err">{take.error}</div>}
              </div>
            )}
          </div>
        );
      })}
      <button className="b" onClick={() => update((p) => { p.shots.push({ id: uid(), prompt: "", seconds: 15, refIds: [], takes: [], selected: 0, include: true }); })}>+ Add shot</button>
    </>
  );
}

// ---- step 6: combine ------------------------------------------------------------

function CombineStep({ project, update }) {
  const c = project.combine;
  const included = project.shots.filter((s) => s.include !== false);
  const missing = included.filter((s) => !selectedTake(s));

  async function run() {
    update((p) => { p.combine = { ...p.combine, status: "running", error: "" }; });
    try {
      const r = await api("/api/combine", { video_urls: included.map((s) => selectedTake(s).video) });
      update((p) => { p.combine = { url: r.url, duration: r.duration, spans: r.spans, status: "done", error: "", shots: included.map((s) => s.id) }; });
    } catch (e) { update((p) => { p.combine.status = "failed"; p.combine.error = e.message; }); }
  }
  const stale = c.url && (c.shots || []).join() !== included.map((s) => s.id).join();

  return (
    <>
      <p className="sub">Each included shot's video is joined in order with ffmpeg — a preview of the full cut. The redub (step 7) combines each of its parts itself; untick a shot here to leave it out of both.</p>
      <div className="card">
        <table className="vt">
          <thead><tr><th>Include</th><th>Shot</th><th>Video</th><th>Duration</th></tr></thead>
          <tbody>
            {project.shots.map((s, i) => {
              const t = selectedTake(s);
              return (
                <tr key={s.id}>
                  <td><input type="checkbox" checked={s.include !== false} onChange={(e) => { const v = e.target.checked; update((p) => { p.shots.find((y) => y.id === s.id).include = v; }); }} /></td>
                  <td>Shot {shotLetter(i)}</td>
                  <td>{t ? <span className="pill ok">Ready</span> : <span className="pill bad">Not generated</span>}</td>
                  <td>{s.seconds}s</td>
                </tr>
              );
            })}
          </tbody>
        </table>
        <div className="row" style={{ marginTop: 12 }}>
          <button className="b primary" disabled={!included.length || missing.length > 0 || c.status === "running"} onClick={run}>
            {c.status === "running" ? <><span className="spinner" /> Combining…</> : `Combine ${included.length} shot${included.length === 1 ? "" : "s"}`}
          </button>
          <span className="note">≈ {Math.round(included.reduce((a, s) => a + (Number(s.seconds) || 0), 0))} s planned</span>
          {missing.length > 0 && <span className="note">Every included shot needs a finished video.</span>}
          {stale && <span className="pill bad">Shots changed since this was combined</span>}
        </div>
        {c.error && <div className="err">{c.error}</div>}
      </div>
      {c.url && (
        <div className="card">
          <h3>Combined video
            {c.duration ? <span className={"pill" + (c.duration >= 360 ? " bad" : " ok")}>{fmtTime(c.duration)}{c.duration >= 360 ? " · over Seed Audio's 6-minute limit" : ""}</span> : null}
            <a className="b sm" href={c.url} download style={{ textDecoration: "none" }}>Download</a></h3>
          <video src={c.url} controls style={{ maxWidth: 820 }} />
        </div>
      )}
    </>
  );
}

// ---- step 7: redub --------------------------------------------------------------

// Every preset voice with a sample to play, so a voice can be heard before it is chosen; "Assign to"
// gives it to a character straight from the card.
function VoiceGallery({ people, voices, setVoice }) {
  const audio = useRef(null);
  const [playing, setPlaying] = useState("");
  useEffect(() => () => { if (audio.current) audio.current.pause(); }, []);
  function toggle(x) {
    if (!audio.current) {
      audio.current = new Audio();
      audio.current.onended = () => setPlaying("");
    }
    if (playing === x.id) { audio.current.pause(); setPlaying(""); return; }
    audio.current.src = `/static/voice-sample-${x.name.toLowerCase()}.wav`;
    audio.current.play().then(() => setPlaying(x.id)).catch(() => setPlaying(""));
  }
  return (
    <div className="voice-gallery">
      {VOICE_PRESETS.map((x) => {
        const users = people.filter((c) => { const v = voices[c.id]; return v && v.kind === "preset" && v.preset === x.id; });
        return (
          <div key={x.id} className={"voice-card" + (playing === x.id ? " playing" : "")}>
            <button className="voice-play" onClick={() => toggle(x)} title={playing === x.id ? "Stop" : `Play ${x.name}`}>
              {playing === x.id ? "■" : "▶"}
            </button>
            <div className="voice-info">
              <b>{x.name}</b>
              <span className="note">{x.about}</span>
              {users.length > 0 && <span className="note">Used by: {users.map((c) => c.name).join(", ")}</span>}
            </div>
            <select value="" onChange={(e) => { const cid = e.target.value; if (cid) setVoice(cid, (v) => { v.kind = "preset"; v.preset = x.id; }); }}>
              <option value="">Assign to…</option>
              {people.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
          </div>
        );
      })}
    </div>
  );
}

function DubStep({ project, update, config }) {
  const d = project.dub;
  const voices = mappedVoices(project);
  const limits = (config && config.dub_limits) || { prompt_chars: 10000, video_seconds: 360 };
  const setVoice = (cid, fn) => update((p) => { p.dub.voices[cid] = { kind: "none", ...(p.dub.voices[cid] || {}) }; fn(p.dub.voices[cid]); });
  const parts = d.parts || [];
  const setPart = (id, fn) => update((p) => { const x = (p.dub.parts || []).find((y) => y.id === id); if (x) fn(x); });
  const included = project.shots.filter((s) => s.include !== false);
  const planned = parts.flatMap((x) => x.shotIds).join() === included.map((s) => s.id).join();
  // Characters who appear in the parts (their sheet is mapped to an included shot) but have no voice.
  const onScreen = new Set(project.library.items.filter((it) => it.kind === "character" && included.some((s) => (s.refIds || []).includes(it.id)))
    .map((it) => (it.subject || "").toLowerCase()));
  const unvoiced = parts.length > 1 ? project.characters.filter((c) => onScreen.has(c.name.toLowerCase()) && !voiceRef(d.voices[c.id])) : [];
  const allDone = parts.length > 0 && parts.every((x) => x.result && x.result.final);
  const finalUrl = d.final && d.final.url;

  function plan() {
    if (parts.some((x) => x.result) && !confirm("Re-plan the parts? Redubbed parts are discarded.")) return;
    update((p) => { p.dub.parts = planParts(p); p.dub.final = null; });
  }

  // Splitting or merging parts invalidates only the parts it touches.
  function splitAfter(part, k) {
    update((p) => {
      const i = p.dub.parts.findIndex((x) => x.id === part.id);
      p.dub.parts.splice(i, 1, newPart(part.shotIds.slice(0, k + 1)), newPart(part.shotIds.slice(k + 1)));
      p.dub.final = null;
    });
  }
  function mergeNext(part) {
    update((p) => {
      const i = p.dub.parts.findIndex((x) => x.id === part.id);
      const next = p.dub.parts[i + 1];
      if (next) p.dub.parts.splice(i, 2, newPart([...part.shotIds, ...next.shotIds]));
      p.dub.final = null;
    });
  }

  async function runPart(part, stopAfter) {
    const shots = partShots(project, part);
    const videos = shots.map((s) => (selectedTake(s) || {}).video);
    if (videos.some((v) => !v)) { alert("Every shot in this part needs a finished video (step 5)."); return; }
    const combined = part.combined && (part.combined.videos || []).join() === videos.join() ? part.combined : null;
    setPart(part.id, (x) => { x.job = { status: "running", stage: "Starting" }; if (!stopAfter) x.result = null; });
    try {
      const { job_id } = await api("/api/dub/part", {
        shots: shots.map((s, i) => ({ label: shotLetter(project.shots.indexOf(s)), video: videos[i], prompt: s.prompt })),
        header: dubHeader(project), voices, combined, optimize: d.optimize || "auto",
        prompt_override: part.edited && !stopAfter ? part.prompt : "", stop_after: stopAfter || "",
        output: { format: d.format, sample_rate: d.sampleRate },
        project: project.name, part: `Part ${parts.indexOf(part) + 1}`,
      });
      setPart(part.id, (x) => { x.job = { job_id, status: "running", stage: "Starting", videos }; });
    } catch (e) { setPart(part.id, (x) => { x.job = { status: "failed", error: e.message }; }); }
    update((p) => { p.dub.final = null; });
  }

  async function join() {
    update((p) => { p.dub.final = { status: "running" }; });
    try {
      const urls = parts.map((x) => x.result.final);
      const r = urls.length === 1 ? { url: urls[0] } : await api("/api/combine", { video_urls: urls });
      update((p) => { p.dub.final = { url: r.url, duration: r.duration, parts: parts.map((x) => x.id) }; });
    } catch (e) { update((p) => { p.dub.final = { status: "failed", error: e.message }; }); }
  }

  return (
    <>
      <p className="sub">Each part's shots are combined into one video and sent to Seed Audio 1.5 with the redub pre-prompt and those shots' prompts — CINEMATOGRAPHY removed, every beat time converted to the part's own timeline — so dialogue, music, ambience and effects come back in one track. Long episodes are dubbed in parts of about 3 minutes, split at shot boundaries (at a scene change where possible), then joined.</p>
      <div className="cols">
        <div className="card">
          <h3>Language</h3>
          <input type="text" value={d.language} onChange={(e) => { const v = e.target.value; update((p) => { p.dub.language = v; }); }} placeholder="e.g. Hindi, English" />
          <h3 style={{ marginTop: 16 }}>Preset voices</h3>
          <div className="note">Listen before choosing — play a voice, then assign it to a character.</div>
          <VoiceGallery people={project.characters} voices={project.dub.voices} setVoice={setVoice} />
          <h3 style={{ marginTop: 16 }}>Voices</h3>
          <div className="note">Map a Seed Audio voice to each character. Mapped voices are sent as @Audio1, @Audio2… in this order (max 6).</div>
          <table className="vt">
            <tbody>
              {project.characters.map((c) => {
                const v = d.voices[c.id] || { kind: "none" };
                const preset = VOICE_PRESETS.find((x) => x.id === v.preset);
                const n = voices.findIndex((x) => x.name === c.name);
                return (
                  <tr key={c.id}>
                    <td style={{ width: "28%" }}><b>{c.name}</b>{n >= 0 && <div><span className="pill run">@Audio{n + 1}</span></div>}</td>
                    <td>
                      <div className="row">
                        <select value={v.kind} onChange={(e) => { const k = e.target.value; setVoice(c.id, (x) => { x.kind = k; }); }}>
                          <option value="none">Keep original voice</option>
                          <option value="preset">Preset voice</option>
                          <option value="custom">Voice ID</option>
                          <option value="clip">Reference clip</option>
                        </select>
                        {v.kind === "preset" && (
                          <select value={v.preset || ""} onChange={(e) => { const id = e.target.value; setVoice(c.id, (x) => { x.preset = id; }); }}>
                            <option value="">Choose…</option>
                            {VOICE_PRESETS.map((x) => <option key={x.id} value={x.id}>{x.name} — {x.about}</option>)}
                          </select>
                        )}
                        {v.kind === "custom" && (
                          <input type="text" className="grow" placeholder="e.g. en_female_…_uranus_bigtts" value={v.custom || ""}
                            onChange={(e) => { const id = e.target.value; setVoice(c.id, (x) => { x.custom = id; }); }} />
                        )}
                        {v.kind === "clip" && (
                          <>
                            <FileButton label={v.clip ? "Replace clip" : "Upload WAV/MP3 (2–30 s)"} accept=".wav,.mp3,audio/wav,audio/mpeg" onFile={async (f) => {
                              try {
                                const { url } = await api("/api/voice/upload", { data_url: await readFile(f), filename: f.name });
                                setVoice(c.id, (x) => { x.clip = url; });
                              } catch (e) { alert(e.message); }
                            }} />
                            {v.clip && <audio src={v.clip} controls style={{ height: 30 }} />}
                          </>
                        )}
                      </div>
                      {v.kind === "preset" && preset && <audio src={`/static/voice-sample-${preset.name.toLowerCase()}.wav`} controls style={{ height: 30, marginTop: 6 }} />}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {!project.characters.length && <div className="note">No characters in this project.</div>}
          {voices.length > 6 && <div className="err">Seed Audio takes at most 6 reference voices.</div>}
          <h3 style={{ marginTop: 16 }}>Output</h3>
          <div className="row">
            <select value={d.format} onChange={(e) => { const v = e.target.value; update((p) => { p.dub.format = v; }); }}>
              <option value="wav">WAV</option><option value="mp3">MP3</option>
            </select>
            <select value={d.sampleRate} onChange={(e) => { const v = Number(e.target.value); update((p) => { p.dub.sampleRate = v; }); }}>
              {[24000, 44100, 48000].map((r) => <option key={r} value={r}>{r} Hz</option>)}
            </select>
          </div>
        </div>
        <div className="card">
          <h3>Parts
            <div className="grow" />
            <select value={d.optimize || "auto"} onChange={(e) => { const v = e.target.value; update((p) => { p.dub.optimize = v; }); }} title="Optimize each part's prompt with the Seed Audio 1.5 skill">
              <option value="auto">Seed Audio skill: when over {limits.prompt_chars.toLocaleString()} chars</option>
              <option value="always">Seed Audio skill: always</option>
              <option value="never">Seed Audio skill: never</option>
            </select>
            <button className="b sm" disabled={!included.length} onClick={plan}>{parts.length ? "Re-plan parts" : "Plan parts"}</button>
          </h3>
          {!parts.length && <div className="note">Plan parts to split the {included.length} included shot{included.length === 1 ? "" : "s"} into redub parts. A short episode becomes a single part.</div>}
          {parts.length > 0 && !planned && <div className="banner">The included shots changed since these parts were planned — re-plan to cover the current cut.</div>}
          {unvoiced.length > 0 && <div className="banner">Dubbing in {parts.length} parts: {unvoiced.map((c) => c.name).join(", ")} {unvoiced.length === 1 ? "has" : "have"} no mapped voice, so their voice may change between parts. Map a preset, voice ID or clip to keep it identical.</div>}
          {voices.length > 6 && <div className="err">Seed Audio takes at most 6 reference voices.</div>}
          {parts.map((part, i) => {
            const shots = partShots(project, part);
            const secs = part.combined ? part.combined.duration : shots.reduce((a, s) => a + (Number(s.seconds) || 0), 0);
            const ready = shots.every((s) => selectedTake(s));
            const busy = running(part.job);
            const over = part.prompt.length > limits.prompt_chars;
            return (
              <div key={part.id} className="part-card">
                <div className="row">
                  <b>Part {i + 1}</b>
                  <span className="note">Shots {partLabel(project, part)} · {part.combined ? "" : "≈ "}{fmtTime(secs)}</span>
                  {part.result && part.result.final ? <span className="pill ok">Redubbed</span> : <Status job={part.job} />}
                  <div className="grow" />
                  {i < parts.length - 1 && <button className="b sm" disabled={busy} onClick={() => mergeNext(part)}>Merge with part {i + 2}</button>}
                </div>
                <div className="row part-shots">
                  {shots.map((s, k) => (
                    <span key={s.id} className="row" style={{ gap: 2 }}>
                      <span className={"pill" + (selectedTake(s) ? "" : " bad")}>{shotLetter(project.shots.indexOf(s))}</span>
                      {k < shots.length - 1 && <button className="split-btn" disabled={busy} title={`Split after shot ${shotLetter(project.shots.indexOf(s))}`} onClick={() => splitAfter(part, k)}>✂</button>}
                    </span>
                  ))}
                </div>
                {!ready && <div className="note">Some shots have no video yet (step 5).</div>}
                {part.prompt ? (
                  <>
                    <div className="row" style={{ margin: "8px 0 4px" }}>
                      {part.edited ? <span className="pill">edited</span> : part.optimized ? <span className="pill run">Seed Audio 1.5 skill</span> : <span className="pill">shot prompts</span>}
                      <span className={"pill" + (over ? " bad" : " ok")}>{part.prompt.length.toLocaleString()} / {limits.prompt_chars.toLocaleString()} chars</span>
                      <div className="grow" />
                      {part.edited && <button className="b sm" disabled={busy} onClick={() => setPart(part.id, (x) => { x.edited = false; })}>Use built prompt</button>}
                    </div>
                    <textarea value={part.prompt} style={{ minHeight: 160 }} onChange={(e) => { const v = e.target.value; setPart(part.id, (x) => { x.prompt = v; x.edited = true; }); }} />
                    {(part.missingLines || []).length > 0 && <div className="banner" style={{ marginTop: 6 }}>Check these lines — they aren't word for word in the prompt: {part.missingLines.map((l) => `“${l}”`).join(" · ")}</div>}
                  </>
                ) : <div className="note" style={{ marginTop: 6 }}>The prompt is built when you prepare or redub this part.</div>}
                <div className="row" style={{ marginTop: 8 }}>
                  <button className="b" disabled={busy || !ready} onClick={() => runPart(part, "prompt")}>{part.prompt ? "Rebuild prompt" : "Prepare prompt"}</button>
                  <button className="b primary" disabled={busy || !ready || voices.length > 6} onClick={() => {
                    if (over && part.edited && !confirm(`This prompt is ${part.prompt.length.toLocaleString()} characters; Seed Audio accepts about ${limits.prompt_chars.toLocaleString()}. Send anyway?`)) return;
                    runPart(part, "");
                  }}>{part.result ? "Redub again" : "Redub part"}</button>
                  {busy && <span className="note">{part.job.stage}</span>}
                </div>
                {part.job && part.job.status === "failed" && <div className="err">{part.job.error}</div>}
                {part.job && part.job.status === "failed" && /violates policy/i.test(part.job.error || "") && (
                  <div className="banner" style={{ marginTop: 6 }}>
                    Seed Audio's content check rejected the original audio of one of these shots — usually a false positive on the generated soundtrack.
                    {shots.length > 1 ? " Split this part (✂) and redub the halves to find the shot, then" : " "} regenerate that shot in step 5 to give it a new soundtrack, and redub this part again.
                  </div>
                )}
                {part.result && part.result.final && <video src={part.result.final} controls preload="metadata" style={{ marginTop: 8, maxWidth: 520 }} />}
              </div>
            );
          })}
          {parts.length > 0 && (
            <div className="row" style={{ marginTop: 12 }}>
              <button className="b primary" disabled={parts.every((x) => running(x.job) || (x.result && x.result.final)) || voices.length > 6}
                onClick={() => parts.filter((x) => !running(x.job) && !(x.result && x.result.final)).forEach((x) => runPart(x, ""))}>
                Redub all remaining parts
              </button>
              <button className="b primary" disabled={!allDone || (d.final && d.final.status === "running")} onClick={join}>
                {parts.length > 1 ? `Join ${parts.length} parts into the episode` : "Use as final video"}
              </button>
              <span className="note">Seed Audio runs two parts at a time; the rest wait their turn.</span>
            </div>
          )}
          {d.final && d.final.status === "failed" && <div className="err">{d.final.error}</div>}
        </div>
      </div>
      {finalUrl && (
        <div className="card">
          <h3>Final episode {d.final.duration ? <span className="pill ok">{fmtTime(d.final.duration)}</span> : null}</h3>
          <video src={finalUrl} controls style={{ maxWidth: 820 }} />
          <div className="row" style={{ marginTop: 10 }}>
            <a className="b primary" href={finalUrl} download style={{ textDecoration: "none" }}>Download final video</a>
            {parts.length > 1 && <span className="note">{parts.length} redubbed parts joined in order.</span>}
          </div>
        </div>
      )}
    </>
  );
}

// ---- standalone tools -----------------------------------------------------------
// Video Dubbing, Video Translation and Audiobook work on their own, outside any project. Each tool's
// form and results are saved on the server (outputs/tools.json) so they survive reloads.

function useToolState(key, defaults) {
  const [state, setState] = useState(null);
  useEffect(() => {
    api("/api/tools/get", { key }).then((r) => setState({ ...defaults, ...(r.state || {}) })).catch(() => setState({ ...defaults }));
  }, [key]);
  useEffect(() => {
    if (!state) return;
    const t = setTimeout(() => { api("/api/tools/save", { key, state }).catch(() => {}); }, 600);
    return () => clearTimeout(t);
  }, [state]);
  const update = (fn) => setState((prev) => { const next = clone(prev || defaults); return fn(next) || next; });
  return [state, update];
}

// Follows a background job until it ends; onChange gets each status.
function useJobPoll(job, onChange) {
  const cb = useRef(onChange);
  cb.current = onChange;
  const id = job && running(job) ? job.job_id : "";
  useEffect(() => {
    if (!id) return;
    let alive = true;
    const tick = () => api("/api/jobs/status", { job_id: id }).then((r) => alive && cb.current(r, null)).catch((e) => alive && cb.current(null, e));
    const iv = setInterval(tick, 4000);
    tick();
    return () => { alive = false; clearInterval(iv); };
  }, [id]);
}

function VideoPicker({ value, onChange, disabled }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [url, setUrl] = useState("");
  return (
    <div>
      <div className="row">
        <FileButton className="b" label={busy ? "Uploading…" : value ? "Replace video" : "Upload video"} accept="video/*" disabled={busy || disabled} onFile={async (f) => {
          setBusy(true); setErr("");
          try { onChange(await uploadFile(f, f.name.replace(/\.[^.]+$/, ""))); } catch (e) { setErr(e.message); }
          setBusy(false);
        }} />
        <span className="note">or</span>
        <input type="text" className="grow" placeholder="Paste a public video URL (https://…)" value={url} onChange={(e) => setUrl(e.target.value)} disabled={disabled} />
        <button className="b sm" disabled={!/^https?:\/\//.test(url) || disabled} onClick={() => { onChange(url.trim()); setUrl(""); }}>Use URL</button>
      </div>
      {err && <div className="err">{err}</div>}
      {value && <video src={value} controls preload="metadata" style={{ marginTop: 10, maxWidth: 560 }} />}
    </div>
  );
}

const ResultVideos = ({ source, result }) => (
  <div className="card">
    <h3>Result</h3>
    <div className="cols">
      <div><div className="note">Original</div><video src={source} controls preload="metadata" /></div>
      <div><div className="note">New audio</div><video src={result.final} controls preload="metadata" /></div>
    </div>
    <div className="row" style={{ marginTop: 10 }}>
      <a className="b primary" href={result.final} download>Download video</a>
      <a className="b" href={result.audio} download>Download audio</a>
      {(result.tracks || []).length > 1 && <span className="note">Tracks: {result.tracks.map((t) => t.type).join(", ")}</span>}
    </div>
  </div>
);

// The full error text from Seed Audio, plus what to try for the ones seen in practice.
function JobError({ job }) {
  if (!job || job.status !== "failed") return null;
  const e = job.error || "Unknown error";
  const hint = /violates policy/i.test(e) ? "Seed Audio's content check rejected an input (usually the video's original audio). Try a different video or regenerate it."
    : /speech content .* not fully specified/i.test(e) ? "Seed Audio needs the words: write every line to be spoken in the prompt."
    : /internal error/i.test(e) ? "A Seed Audio service error — try again; if it repeats, share the request id with the Seed Audio team."
    : /api key/i.test(e) ? "Check SEED_AUDIO_API_KEY in .env and restart the server." : "";
  return <div className="err"><b>Error:</b> {e}{hint ? <div style={{ marginTop: 4 }}>{hint}</div> : null}</div>;
}

function VoiceRows({ people, voices, setVoice, onRename, onRemove }) {
  return (
    <table className="vt">
      <tbody>
        {people.map((c, n) => {
          const v = voices[c.id] || { kind: "none" };
          const preset = VOICE_PRESETS.find((x) => x.id === v.preset);
          return (
            <tr key={c.id}>
              <td style={{ width: "30%" }}>
                {onRename ? <input type="text" value={c.name} onChange={(e) => onRename(c.id, e.target.value)} /> : <b>{c.name}</b>}
                {voiceRef(v) && <div style={{ marginTop: 4 }}><span className="pill run">@Audio{people.slice(0, n + 1).filter((x) => voiceRef(voices[x.id])).length}</span></div>}
              </td>
              <td>
                <div className="row">
                  <select value={v.kind} onChange={(e) => { const k = e.target.value; setVoice(c.id, (x) => { x.kind = k; }); }}>
                    <option value="none">Keep original voice</option>
                    <option value="preset">Preset voice</option>
                    <option value="custom">Voice ID</option>
                    <option value="clip">Reference clip</option>
                  </select>
                  {v.kind === "preset" && (
                    <select value={v.preset || ""} onChange={(e) => { const id = e.target.value; setVoice(c.id, (x) => { x.preset = id; }); }}>
                      <option value="">Choose…</option>
                      {VOICE_PRESETS.map((x) => <option key={x.id} value={x.id}>{x.name} — {x.about}</option>)}
                    </select>
                  )}
                  {v.kind === "custom" && <input type="text" className="grow" placeholder="e.g. en_female_…_uranus_bigtts" value={v.custom || ""} onChange={(e) => { const id = e.target.value; setVoice(c.id, (x) => { x.custom = id; }); }} />}
                  {v.kind === "clip" && (
                    <>
                      <FileButton label={v.clip ? "Replace clip" : "Upload WAV/MP3 (2–30 s)"} accept=".wav,.mp3,audio/wav,audio/mpeg" onFile={async (f) => {
                        try { const { url } = await api("/api/voice/upload", { data_url: await readFile(f), filename: f.name }); setVoice(c.id, (x) => { x.clip = url; }); } catch (e) { alert(e.message); }
                      }} />
                      {v.clip && <audio src={v.clip} controls style={{ height: 30 }} />}
                    </>
                  )}
                  {onRemove && <button className="b sm danger" onClick={() => onRemove(c.id)}>Remove</button>}
                </div>
                {v.kind === "preset" && preset && <audio src={`/static/voice-sample-${preset.name.toLowerCase()}.wav`} controls style={{ height: 30, marginTop: 6 }} />}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

const DUB_TOOL_DEFAULTS = { video: "", prompt: "", language: "English", speakers: [{ id: "s1", name: "Speaker 1" }], voices: {}, optimize: false, format: "wav", sampleRate: 44100, job: null, result: null };

function VideoDubbingTool({ config }) {
  const [s, update] = useToolState("video-dubbing", DUB_TOOL_DEFAULTS);
  useJobPoll(s && s.job, (r, e) => update((q) => {
    if (!q.job) return;
    if (e) { q.job = { status: "failed", error: e.message }; return; }
    q.job = { ...q.job, status: r.status, stage: r.stage, error: r.error };
    const got = r.status === "done" ? r.result : r.partial;
    if (got && got.prompt && got.optimized) { q.sentPrompt = got.prompt; q.missingLines = got.missing_lines || []; }
    if (r.status === "done") q.result = r.result.result;
  }));
  if (!s) return <div className="video-hint">Loading…</div>;
  const limits = (config && config.dub_limits) || { prompt_chars: 10000 };
  const people = s.speakers;
  const mapped = people.map((c) => ({ name: c.name, ref: voiceRef(s.voices[c.id]) })).filter((v) => v.ref);
  const setVoice = (id, fn) => update((q) => { q.voices[id] = { kind: "none", ...(q.voices[id] || {}) }; fn(q.voices[id]); });
  function template() {
    let t = DUB_TEMPLATE.replace("[language]", s.language.trim() || "[language]").replace("[both] characters’", people.length === 2 ? "both characters’" : "all characters’");
    if (mapped.length) t += "\n" + mapped.map((v, i) => `${v.name}’s voice must match @Audio${i + 1}.`).join(" ");
    return t + "\n\nDialogue, in order:\n" + people.map((c) => `${c.name}: "…"`).join("\n");
  }
  async function run() {
    try {
      const { job_id } = await api("/api/tools/dub", { video: s.video, prompt: s.prompt, voices: mapped, optimize: s.optimize, output: { format: s.format, sample_rate: s.sampleRate } });
      update((q) => { q.job = { job_id, status: "running", stage: "Starting" }; q.result = null; q.sentPrompt = ""; q.missingLines = []; q.source = q.video; });
    } catch (e) { update((q) => { q.job = { status: "failed", error: e.message }; }); }
  }
  return (
    <>
      <div className="tool-heading">Video Dubbing</div>
      <p className="sub">Redub any video with Seed Audio 1.5: write the lines to be spoken (and any music, ambience or effects), optionally give characters reference voices, and the returned track replaces the video's audio. Videos must be shorter than 6 minutes.</p>
      <div className="card"><h3>Video</h3><VideoPicker value={s.video} onChange={(v) => update((q) => { q.video = v; })} /></div>
      <div className="cols">
        <div className="card">
          <h3>Language</h3>
          <input type="text" value={s.language} onChange={(e) => { const v = e.target.value; update((q) => { q.language = v; }); }} placeholder="e.g. English, Hindi" />
          <h3 style={{ marginTop: 16 }}>Preset voices</h3>
          <div className="note">Listen before choosing — play a voice, then assign it to a speaker.</div>
          <VoiceGallery people={people} voices={s.voices} setVoice={setVoice} />
          <h3 style={{ marginTop: 16 }}>Speakers <button className="b sm" onClick={() => update((q) => { q.speakers.push({ id: uid(), name: `Speaker ${q.speakers.length + 1}` }); })}>+ Add speaker</button></h3>
          <div className="note">Mapped voices are sent as @Audio1, @Audio2… in this order (max 6).</div>
          <VoiceRows people={people} voices={s.voices} setVoice={setVoice}
            onRename={(id, name) => update((q) => { q.speakers.find((x) => x.id === id).name = name; })}
            onRemove={people.length > 1 ? (id) => update((q) => { q.speakers = q.speakers.filter((x) => x.id !== id); delete q.voices[id]; }) : null} />
          <h3 style={{ marginTop: 16 }}>Output</h3>
          <div className="row">
            <select value={s.format} onChange={(e) => { const v = e.target.value; update((q) => { q.format = v; }); }}><option value="wav">WAV</option><option value="mp3">MP3</option></select>
            <select value={s.sampleRate} onChange={(e) => { const v = Number(e.target.value); update((q) => { q.sampleRate = v; }); }}>{[24000, 44100, 48000].map((r) => <option key={r} value={r}>{r} Hz</option>)}</select>
          </div>
        </div>
        <div className="card">
          <h3>Prompt
            <span className={"pill" + (s.prompt.length > limits.prompt_chars ? " bad" : " ok")}>{s.prompt.length.toLocaleString()} / {limits.prompt_chars.toLocaleString()} chars</span>
            <div className="grow" />
            <button className="b sm" onClick={() => update((q) => { q.prompt = template() + (q.prompt.trim() ? "\n\n" + q.prompt : ""); })}>Insert redub template</button>
          </h3>
          <textarea className="tall" style={{ minHeight: 360 }} value={s.prompt} placeholder="Every line to be spoken, in order, with who says it — plus any music, ambience or effects. Seed Audio refuses a dub without the words."
            onChange={(e) => { const v = e.target.value; update((q) => { q.prompt = v; }); }} />
          <label className="row note" style={{ gap: 6, marginTop: 8, cursor: "pointer" }}>
            <input type="checkbox" checked={!!s.optimize} onChange={(e) => { const v = e.target.checked; update((q) => { q.optimize = v; }); }} />
            Optimize with the Seed Audio 1.5 skill before dubbing (one sound script, lines verbatim, under the limit)
          </label>
          <div className="row" style={{ marginTop: 10 }}>
            <button className="b primary" disabled={!s.video || !s.prompt.trim() || running(s.job) || mapped.length > 6} onClick={run}>Dub video</button>
            <Status job={s.job} />
          </div>
          <JobError job={s.job} />
          {s.sentPrompt && <details style={{ marginTop: 8 }}><summary>Optimized prompt sent to Seed Audio</summary><pre className="p">{s.sentPrompt}</pre></details>}
          {(s.missingLines || []).length > 0 && <div className="banner" style={{ marginTop: 6 }}>Not word for word in the optimized prompt: {s.missingLines.map((l) => `“${l}”`).join(" · ")}</div>}
        </div>
      </div>
      {s.result && s.result.final && <ResultVideos source={s.source || s.video} result={s.result} />}
    </>
  );
}

const TRANSLATE_DEFAULTS = { video: "", target: "en", glossaries: [], job: null, result: null };

function VideoTranslationTool() {
  const [s, update] = useToolState("video-translation", TRANSLATE_DEFAULTS);
  const [langs, setLangs] = useState([]);
  useEffect(() => { api("/api/tools/languages", {}).then((r) => setLangs(r.languages)).catch(() => {}); }, []);
  useJobPoll(s && s.job, (r, e) => update((q) => {
    if (!q.job) return;
    if (e) { q.job = { status: "failed", error: e.message }; return; }
    q.job = { ...q.job, status: r.status, stage: r.stage, error: r.error };
    if (r.status === "done") q.result = r.result;
  }));
  if (!s) return <div className="video-hint">Loading…</div>;
  async function run() {
    try {
      const { job_id } = await api("/api/tools/translate", { video: s.video, target_language: s.target, glossaries: s.glossaries });
      update((q) => { q.job = { job_id, status: "running", stage: "Starting" }; q.result = null; q.source = q.video; });
    } catch (e) { update((q) => { q.job = { status: "failed", error: e.message }; }); }
  }
  return (
    <>
      <div className="tool-heading">Video Translation</div>
      <p className="sub">Dub a video into another language with Seed Audio 1.5 — no prompt needed: it keeps each speaker's voice and timing and speaks the dialogue in the target language. A glossary pins how names and terms are translated. Videos must be shorter than 6 minutes.</p>
      <div className="card"><h3>Video</h3><VideoPicker value={s.video} onChange={(v) => update((q) => { q.video = v; })} /></div>
      <div className="card">
        <h3>Target language</h3>
        <select value={s.target} onChange={(e) => { const v = e.target.value; update((q) => { q.target = v; }); }}>
          {langs.map(([code, name]) => <option key={code} value={code}>{name}</option>)}
        </select>
        <h3 style={{ marginTop: 16 }}>Glossary <button className="b sm" onClick={() => update((q) => { q.glossaries.push({ source: "", target: "" }); })}>+ Add term</button></h3>
        <div className="note">Optional: how specific names or terms must be translated.</div>
        {s.glossaries.map((g, i) => (
          <div className="row" key={i} style={{ marginTop: 6 }}>
            <input type="text" className="grow" placeholder="Source term" value={g.source} onChange={(e) => { const v = e.target.value; update((q) => { q.glossaries[i].source = v; }); }} />
            <span className="note">→</span>
            <input type="text" className="grow" placeholder="Translation" value={g.target} onChange={(e) => { const v = e.target.value; update((q) => { q.glossaries[i].target = v; }); }} />
            <button className="b sm danger" onClick={() => update((q) => { q.glossaries.splice(i, 1); })}>Remove</button>
          </div>
        ))}
        <div className="row" style={{ marginTop: 14 }}>
          <button className="b primary" disabled={!s.video || running(s.job)} onClick={run}>Translate video</button>
          <Status job={s.job} />
        </div>
        <JobError job={s.job} />
      </div>
      {s.result && s.result.final && <ResultVideos source={s.source || s.video} result={s.result} />}
    </>
  );
}

function AudiobookPage() {
  const [s, update] = useToolState("audiobook", {});
  if (!s) return <div className="video-hint">Loading…</div>;
  if (!window.AudiobookTool) return <div className="err">The Audiobook tool failed to load.</div>;
  // The audiobook component writes through update(prev => next).
  const write = (fn) => update((prev) => fn(prev));
  return (
    <>
      <div className="tool-heading">Audiobook</div>
      <window.AudiobookTool state={s} update={write} />
    </>
  );
}

const TOOLS = [
  { key: "video-dubbing", label: "Video Dubbing", icon: "dub", Page: VideoDubbingTool },
  { key: "video-translation", label: "Video Translation", icon: "translate", Page: VideoTranslationTool },
  { key: "audiobook", label: "Audiobook", icon: "book", Page: AudiobookPage },
];

// ---- app ------------------------------------------------------------------------

// Sidebar icons.
const ICONS = {
  projects: <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />,
  script: <><path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z" /><path d="M14 3v6h6" /><path d="M8 13h8M8 17h5" /></>,
  characters: <><circle cx="12" cy="8" r="4" /><path d="M4 21a8 8 0 0 1 16 0" /></>,
  locations: <><path d="M12 21s-7-6.3-7-12a7 7 0 0 1 14 0c0 5.7-7 12-7 12z" /><circle cx="12" cy="9" r="2.5" /></>,
  library: <><rect x="3" y="3" width="7" height="7" rx="1" /><rect x="14" y="3" width="7" height="7" rx="1" /><rect x="3" y="14" width="7" height="7" rx="1" /><rect x="14" y="14" width="7" height="7" rx="1" /></>,
  shots: <><rect x="2" y="6" width="14" height="12" rx="2" /><path d="M16 10l6-3v10l-6-3z" /></>,
  combine: <><rect x="2" y="4" width="20" height="16" rx="2" /><path d="M7 4v16M17 4v16M2 12h20" /></>,
  dub: <><path d="M12 3a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V6a3 3 0 0 0-3-3z" /><path d="M19 11a7 7 0 0 1-14 0M12 18v3" /></>,
  translate: <><path d="M4 5h8M8 3v2M6 5c0 4 2 7 5 8M10 5c0 3-2 7-6 9" /><path d="M13 21l4-9 4 9M14.5 18h5" /></>,
  book: <><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" /><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z" /></>,
};
const Icon = ({ name }) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{ICONS[name]}</svg>
);

const STEPS = [
  { key: "script", label: "Script to Prompt" },
  { key: "characters", label: "Character Sheets" },
  { key: "locations", label: "Location Sheets" },
  { key: "library", label: "Asset Library" },
  { key: "shots", label: "Shots · Long Duration" },
  { key: "combine", label: "Combine Shots" },
  { key: "dub", label: "Seed Audio Redub" },
];

// The chip and one-line preview in each tool card's header.
function stepHeader(key, p) {
  const runningAny = (xs) => xs.some(running);
  switch (key) {
    case "script":
      if (running(p.script.job)) return { status: p.script.job.stage || "Running", busy: true };
      return p.shots.length ? { status: `${p.shots.length} shots · ${p.characters.length} characters · ${p.locations.length} locations`, preview: p.script.text.trim().split("\n")[0] }
        : { preview: "Paste a script to begin" };
    case "characters": {
      const done = p.characters.filter((c) => c.sheet && c.sheet.image).length;
      return { status: p.characters.length ? `${done}/${p.characters.length} sheets` : "", busy: runningAny(p.characters.map((c) => c.sheet)), preview: p.characters.map((c) => c.name).join(", ") };
    }
    case "locations": {
      const done = p.locations.filter((l) => l.plate).length;
      return { status: p.locations.length ? `${done}/${p.locations.length} plates` : "", busy: runningAny(p.locations.map((l) => l.job)), preview: p.locations.map((l) => l.name).join(", ") };
    }
    case "library": {
      const ready = p.library.items.filter((it) => it.status === "ready").length;
      return { status: p.library.items.length ? `${ready} assets ready` : "", busy: p.library.items.some((it) => it.status === "uploading"), preview: p.library.items.map((it) => it.name).join(", ") };
    }
    case "shots": {
      const done = p.shots.filter((s) => selectedTake(s)).length;
      return { status: p.shots.length ? `${done}/${p.shots.length} shots ready` : "", busy: p.shots.some((s) => running(s.job) || running((s.takes || [])[s.selected])) };
    }
    case "combine":
      return { status: p.combine.status === "running" ? "Combining" : p.combine.url ? "Combined" : "", busy: p.combine.status === "running" };
    case "dub":
      {
        const ps = p.dub.parts || [];
        const done = ps.filter((x) => x.result && x.result.final).length;
        const status = p.dub.final && p.dub.final.url ? "Final video ready" : ps.length ? `${done}/${ps.length} parts redubbed` : "";
        return { status, busy: ps.some((x) => running(x.job)), preview: [p.dub.language, mappedVoices(p).map((v) => v.name).join(", ")].filter(Boolean).join(" · ") };
      }
    default: return {};
  }
}

function stepDone(key, p) {
  switch (key) {
    case "script": return p.shots.length > 0;
    case "characters": return p.characters.length > 0 && p.characters.every((c) => c.sheet && c.sheet.image);
    case "locations": return p.locations.length > 0 && p.locations.every((l) => l.plate);
    case "library": return p.library.items.some((it) => it.status === "ready");
    case "shots": return p.shots.length > 0 && p.shots.every((s) => s.include === false || selectedTake(s));
    case "combine": return !!p.combine.url;
    case "dub": return !!(p.dub.final && p.dub.final.url);
    default: return false;
  }
}

function App() {
  const [config, setConfig] = useState(null);
  const [projects, setProjects] = useState([]);
  const [project, setProject] = useState(null);
  const [loaded, setLoaded] = useState(false);
  const [tool, setTool] = useState(() => { try { return localStorage.getItem("sdp-tool") || ""; } catch (e) { return ""; } });
  const [query, setQuery] = useState("");
  const [zoomSrc, setZoom] = useState("");
  const [saveState, setSaveState] = useState("");
  const projectRef = useRef(null);
  projectRef.current = project;
  const libQueue = useRef(Promise.resolve());

  const update = (fn) => setProject((prev) => { if (!prev) return prev; const next = clone(prev); fn(next); return next; });

  useEffect(() => {
    api("/api/config").then(setConfig).catch(() => {});
    api("/api/projects/list").then(async ({ projects }) => {
      setProjects(projects);
      let last = "";
      try { last = localStorage.getItem("sdp-project") || ""; } catch (e) { /* storage blocked */ }
      const pick = projects.find((p) => p.id === last);
      if (pick) setProject(migrateDub((await api("/api/projects/get", { id: pick.id })).project));
      setLoaded(true);
    }).catch(() => setLoaded(true));
  }, []);

  useEffect(() => {
    try { localStorage.setItem("sdp-project", project ? project.id : ""); } catch (e) { /* storage blocked */ }
  }, [project && project.id]);

  // Autosave, debounced.
  useEffect(() => {
    if (!project) return;
    setSaveState("saving");
    const t = setTimeout(() => {
      api("/api/projects/save", { project }).then(() => {
        setSaveState("saved");
        setProjects((list) => [{ id: project.id, name: project.name, shots: project.shots.length, updated: Date.now() },
          ...list.filter((x) => x.id !== project.id)]);
      }).catch(() => setSaveState("error"));
    }, 700);
    return () => clearTimeout(t);
  }, [project]);

  // One poller for every running job in the project.
  useEffect(() => {
    let alive = true;
    async function poll(fetcher, apply) {
      try { const r = await fetcher(); if (alive) apply(r, null); } catch (e) { if (alive) apply(null, e); }
    }
    async function tick() {
      const p = projectRef.current;
      if (!p) return;
      const work = [];
      const sj = p.script.job;
      if (running(sj) && sj.job_id) work.push(poll(() => api("/api/jobs/status", { job_id: sj.job_id }), (r, e) => update((q) => {
        if (!q.script.job || q.script.job.job_id !== sj.job_id) return;
        if (e) { q.script.job = { status: "failed", error: e.message }; return; }
        q.script.job = { job_id: r.id, status: r.status, stage: r.stage, error: r.error };
        if (r.status === "done") {
          const res = r.result;
          q.shots = res.shots.map((s) => ({ id: uid(), prompt: s.prompt, seconds: s.seconds, refIds: [], takes: [], selected: 0, include: true }));
          q.characters = res.characters.map((c) => ({ id: uid(), name: c.name, role: c.role, description: c.description, refs: [], sheet: null }));
          q.locations = res.locations.map((l) => ({ id: uid(), name: l.name, scenes: l.scenes, direction: l.direction, variations: 3, job: null, plate: "", images: [] }));
          q.dub.voices = {}; q.dub.parts = []; q.dub.final = null;
          q.combine = { url: "", status: "", error: "" };
        }
      })));
      for (const c of p.characters) {
        const s = c.sheet;
        if (!running(s) || !s.job_id) continue;
        work.push(poll(() => api("/api/sheet/status", { job_id: s.job_id }), (r, e) => update((q) => {
          const x = q.characters.find((y) => y.id === c.id);
          if (!x || !x.sheet || x.sheet.job_id !== s.job_id) return;
          if (e) { x.sheet.status = "failed"; x.sheet.error = e.message; return; }
          x.sheet.stage = r.stage; x.sheet.error = r.error || "";
          if (r.status === "done" && r.result) {
            x.sheet.status = "done"; x.sheet.image = r.result.image;
            x.sheet.verdict = r.inspected ? (r.result.passed ? `Passed all ${r.result.criteria_total} checks` : `Best attempt · ${r.result.criteria_passed}/${r.result.criteria_total} checks`) : "";
          } else if (r.status === "failed" || r.status === "error") x.sheet.status = "failed";
        })));
      }
      for (const l of p.locations) {
        const j = l.job;
        if (!running(j) || !j.job_id) continue;
        work.push(poll(() => api("/api/jobs/status", { job_id: j.job_id }), (r, e) => update((q) => {
          const x = q.locations.find((y) => y.id === l.id);
          if (!x || !x.job || x.job.job_id !== j.job_id) return;
          if (e) { x.job = { status: "failed", error: e.message }; return; }
          x.job = { job_id: j.job_id, status: r.status, stage: r.stage, error: r.error };
          if (r.status === "done") { x.plate = r.result.plate; x.images = r.result.variations; }
        })));
      }
      for (const s of p.shots) {
        // One video per shot: only the shot's current generation is followed.
        for (const t of [(s.takes || [])[s.selected]]) {
          if (!running(t)) continue;
          work.push(poll(() => api("/api/shot/status", { task_id: t.task_id, project: p.name, shot: shotLetter(p.shots.indexOf(s)) }), (r, e) => update((q) => {
            const x = q.shots.find((y) => y.id === s.id);
            const tk = x && x.takes.find((y) => y.task_id === t.task_id);
            if (!tk) return;
            if (e) { tk.error = e.message; return; } // transient: keep polling
            tk.status = r.status === "succeeded" ? "done" : r.status === "failed" || r.status === "expired" || r.status === "cancelled" ? "failed" : r.status;
            tk.error = r.error || "";
            if (r.video) { tk.video = r.video; tk.last_frame = r.last_frame || ""; }
          })));
        }
      }
      for (const part of p.dub.parts || []) {
        const j = part.job;
        if (!running(j) || !j.job_id) continue;
        work.push(poll(() => api("/api/jobs/status", { job_id: j.job_id }), (r, e) => update((q) => {
          const x = (q.dub.parts || []).find((y) => y.id === part.id);
          if (!x || !x.job || x.job.job_id !== j.job_id) return;
          if (e) { x.job = { status: "failed", error: e.message }; return; }
          x.job = { ...x.job, status: r.status, stage: r.stage, error: r.error };
          const got = r.status === "done" ? r.result : r.partial;
          if (!got) return;
          if (got.combined) x.combined = { ...got.combined, videos: j.videos || [] };
          if (got.prompt !== undefined && !x.edited) { x.prompt = got.prompt; x.optimized = !!got.optimized; x.missingLines = got.missing_lines || []; }
          if (r.status === "done" && got.result) x.result = got.result;
        })));
      }
      await Promise.all(work);
    }
    const iv = setInterval(tick, 4000);
    return () => { alive = false; clearInterval(iv); };
  }, []);

  // Library uploads run one at a time: the first one creates the asset group.
  function uploadLibraryItem(item) {
    libQueue.current = libQueue.current.then(async () => {
      const p = projectRef.current;
      try {
        const r = await api("/api/library/add", { group_id: p.library.group_id, project_name: p.name, project: p.name, image_url: item.image, name: item.name });
        update((q) => {
          q.library.group_id = r.group_id;
          const x = q.library.items.find((y) => y.id === item.id);
          if (x) { x.asset_id = r.asset_id; x.status = "ready"; x.error = ""; }
        });
        projectRef.current = { ...projectRef.current, library: { ...projectRef.current.library, group_id: r.group_id } };
      } catch (e) {
        update((q) => { const x = q.library.items.find((y) => y.id === item.id); if (x) { x.status = "failed"; x.error = e.message; } });
      }
    });
  }
  function addToLibrary(entry) {
    const item = { id: uid(), status: "uploading", asset_id: "", error: "", label: "", ...entry };
    update((q) => { q.library.items.push(item); });
    uploadLibraryItem(item);
  }
  // Generated sheets and location images not yet in the library (character sheets first, then plates
  // before their variations), and library items whose upload failed.
  function libraryBacklog(p) {
    const have = new Set(p.library.items.map((it) => it.image));
    const fresh = [];
    for (const c of p.characters) {
      if (c.sheet && c.sheet.image && !have.has(c.sheet.image)) fresh.push({ name: `${c.name} — character sheet`, subject: c.name, kind: "character", image: c.sheet.image });
    }
    for (const l of p.locations) {
      const imgs = l.plate ? [{ label: "Plate", url: l.plate }, ...(l.images || [])] : [];
      for (const im of imgs) {
        if (!have.has(im.url)) fresh.push({ name: `${l.name} — ${im.label}`, subject: l.name, label: im.label === "Plate" ? "" : im.label, kind: "location", image: im.url });
      }
    }
    return { fresh, failed: p.library.items.filter((it) => it.status === "failed") };
  }
  function uploadAll() {
    const { fresh, failed } = libraryBacklog(projectRef.current);
    failed.forEach(retryLibrary);
    fresh.forEach(addToLibrary);
  }

  function retryLibrary(item) {
    update((q) => { const x = q.library.items.find((y) => y.id === item.id); x.status = "uploading"; x.error = ""; });
    uploadLibraryItem(item);
  }

  function openTool(key) {
    setTool(key);
    try { localStorage.setItem("sdp-tool", key); } catch (e) { /* storage blocked */ }
    window.scrollTo(0, 0);
  }

  async function openProject(id) {
    openTool("");
    setProject(id ? migrateDub((await api("/api/projects/get", { id })).project) : null);
    window.scrollTo(0, 0);
  }

  async function deleteProject(id, name) {
    if (!confirm(`Delete project “${name}”? Generated files stay in outputs/.`)) return;
    await api("/api/projects/delete", { id });
    setProjects((list) => list.filter((x) => x.id !== id));
    if (project && project.id === id) setProject(null);
  }

  // Sidebar tool click: open the project's card for that step and scroll to it.
  function goToStep(key) {
    if (!project) return;
    if (tool) openTool("");
    update((p) => { p.collapsed = { ...(p.collapsed || {}), [key]: false }; });
    setTimeout(() => { const el = document.getElementById("step-" + key); if (el) el.scrollIntoView({ behavior: "smooth", block: "start" }); }, 30);
  }

  if (!loaded) return <div className="app"><div className="video-hint">Loading…</div></div>;
  const keys = config && config.keys;
  const missing = keys ? [!keys.ark && "ARK_API_KEY", !keys.assets && "BYTEPLUS_AK / BYTEPLUS_SK / TOS_BUCKET_NAME (asset library)", !keys.seed_audio && "SEED_AUDIO_API_KEY"].filter(Boolean) : [];
  const backlog = project ? libraryBacklog(project) : { fresh: [], failed: [] };
  const uploadAllButton = (
    <button className="b" disabled={!backlog.fresh.length && !backlog.failed.length} onClick={uploadAll}
      title="Adds every generated character sheet and location image that isn't in the asset library yet, and retries failed uploads">
      Upload all to asset library ({backlog.fresh.length + backlog.failed.length})
    </button>
  );
  const props = { project, update, addToLibrary, retryLibrary, zoom: setZoom, config, uploadAllButton };
  const STEP_BODY = { script: ScriptStep, characters: CharactersStep, locations: LocationsStep, library: LibraryStep, shots: ShotsStep, combine: CombineStep, dub: DubStep };
  const terms = query.trim().toLowerCase();
  const visible = projects.filter((x) => !terms || (x.name || "").toLowerCase().includes(terms));

  return (
    <div className="app">
      <aside className="sidebar" aria-label="Tools">
        <div className="sidebar-brand">
          <svg viewBox="0 0 32 32" width="28" height="28" aria-hidden="true"><defs><linearGradient id="sdpMark" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stopColor="#4F46E5" /><stop offset="0.55" stopColor="#7C6FE3" /><stop offset="1" stopColor="#EC4899" /></linearGradient></defs><rect width="32" height="32" rx="8" fill="url(#sdpMark)" /><rect x="6.5" y="9.5" width="17" height="14" rx="3" fill="none" stroke="#fff" strokeWidth="2" /><path d="M13 13.4v6.2l5-3.1z" fill="#fff" /><path d="M25 4.5l1.1 2.4 2.4 1.1-2.4 1.1L25 11.5l-1.1-2.4-2.4-1.1 2.4-1.1z" fill="#fff" /></svg>
          Short Drama Pipeline
        </div>
        <div className="sidebar-group">Workspace</div>
        <div className="sidebar-nav">
          <button className={"sidebar-btn" + (!project && !tool ? " active" : "")} onClick={() => openProject(null)}><Icon name="projects" />Projects</button>
        </div>
        <div className="sidebar-group">Pipeline</div>
        <div className="sidebar-nav">
          {STEPS.map((s) => (
            <button key={s.key} className="sidebar-btn" disabled={!project} title={project ? "" : "Open a project first"} onClick={() => goToStep(s.key)}>
              <Icon name={s.key} />
              <span className="grow">{s.label}</span>
              {project && stepDone(s.key, project) && <span className="sidebar-done">✓</span>}
            </button>
          ))}
        </div>
        <div className="sidebar-group">Tools</div>
        <div className="sidebar-nav">
          {TOOLS.map((t) => (
            <button key={t.key} className={"sidebar-btn" + (tool === t.key ? " active" : "")} onClick={() => openTool(t.key)}>
              <Icon name={t.icon} />{t.label}
            </button>
          ))}
        </div>
      </aside>

      {missing.length > 0 && <div className="error-box" style={{ marginTop: 0, marginBottom: 14 }}>Missing credentials: {missing.join(", ")}. Add them to <code>.env</code> and restart.</div>}
      {config && !config.ffmpeg && <div className="error-box" style={{ marginTop: 0, marginBottom: 14 }}>ffmpeg not found — combining shots and replacing audio need it (<code>brew install ffmpeg</code>).</div>}

      {tool ? (() => { const T = TOOLS.find((t) => t.key === tool); return T ? <T.Page config={config} /> : null; })() : !project ? (
        <>
          <div className="proj-list-head">
            <div className="tool-heading" style={{ margin: 0 }}>Projects</div>
            <button className="proj-new-btn" onClick={() => setProject(newProject())}>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round"><path d="M12 5v14" /><path d="M5 12h14" /></svg>
              New Project
            </button>
          </div>
          {projects.length > 0 && (
            <div className="card proj-filter-bar">
              <div className="proj-search">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="11" cy="11" r="7" /><line x1="21" y1="21" x2="16.65" y2="16.65" /></svg>
                <input type="text" placeholder="Search projects by name…" value={query} onChange={(e) => setQuery(e.target.value)} />
              </div>
            </div>
          )}
          <div className="proj-grid">
            {visible.map((x) => (
              <div key={x.id} className="proj-card" onClick={() => openProject(x.id)}>
                <div className="proj-card-top">
                  <div>
                    <div className="proj-card-name">{x.name}</div>
                    <span className="proj-type-badge">Short Drama</span>
                  </div>
                  <button className="proj-delete-btn" title="Delete project" onClick={(e) => { e.stopPropagation(); deleteProject(x.id, x.name); }}>
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" width="14" height="14"><polyline points="3 6 5 6 21 6" /><path d="M19 6l-1 14H6L5 6" /><path d="M10 11v6" /><path d="M14 11v6" /><path d="M9 6V4h6v2" /></svg>
                  </button>
                </div>
                <div className="proj-meta">
                  <span>{x.shots || 0} shots</span>
                  {x.updated ? <span>Updated {new Date(x.updated).toLocaleString()}</span> : null}
                </div>
              </div>
            ))}
            <div className="proj-new-card" role="button" tabIndex="0" onClick={() => setProject(newProject())}>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" width="16" height="16"><path d="M12 5v14" /><path d="M5 12h14" /></svg>
              New Project
            </div>
          </div>
        </>
      ) : (
        <>
          <button className="proj-detail-back" onClick={() => openProject(null)}>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" width="13" height="13"><path d="M19 12H5" /><path d="M12 19l-7-7 7-7" /></svg>
            All Projects
          </button>
          <div className="proj-detail-title">
            <input type="text" className="proj-name-input" value={project.name} onChange={(e) => { const v = e.target.value; update((p) => { p.name = v; }); }} />
            <span className="proj-type-badge">Short Drama</span>
            <span className="grow" />
            <span className="video-hint" style={{ margin: 0 }}>{saveState === "saving" ? "Saving…" : saveState === "saved" ? "Saved" : saveState === "error" ? "Save failed" : ""}</span>
            <button className="proj-delete-btn" title="Delete project" onClick={() => deleteProject(project.id, project.name)}>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" width="14" height="14"><polyline points="3 6 5 6 21 6" /><path d="M19 6l-1 14H6L5 6" /><path d="M10 11v6" /><path d="M14 11v6" /><path d="M9 6V4h6v2" /></svg>
            </button>
          </div>

          <div className="section-heading" style={{ marginTop: 18 }}>Pipeline</div>
          <div className="proj-tools-grid">
            {STEPS.map((s, i) => (
              <button key={s.key} className={"proj-tool-btn" + (stepDone(s.key, project) ? " done" : "")} onClick={() => goToStep(s.key)}>
                <Icon name={s.key} />
                <span className="grow">{i + 1}. {s.label}</span>
                {stepDone(s.key, project) && <span className="sidebar-done">✓</span>}
              </button>
            ))}
          </div>

          <div className="section-heading" style={{ marginTop: 22 }}>Tools</div>
          {STEPS.map((s, i) => {
            const Body = STEP_BODY[s.key];
            const collapsed = !!(project.collapsed || {})[s.key];
            const h = stepHeader(s.key, project);
            return (
              <div className="proj-result-card" id={"step-" + s.key} key={s.key}>
                <button className="proj-result-header" onClick={() => update((p) => { p.collapsed = { ...(p.collapsed || {}), [s.key]: !collapsed }; })}>
                  <div className="proj-result-header-main">
                    <div className="proj-result-title-row">
                      <div className="proj-result-title">{i + 1}. {s.label}</div>
                      {h.status ? <div className={"proj-result-status" + (stepDone(s.key, project) ? " ok" : "")}>{h.busy && <span className="spinner" />} {h.status}</div> : null}
                    </div>
                    {h.preview ? <div className="proj-result-preview">{h.preview}</div> : null}
                  </div>
                  <svg className={"proj-result-chevron" + (collapsed ? " collapsed" : "")} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="6 9 12 15 18 9" /></svg>
                </button>
                {!collapsed && <div className="proj-result-body"><Body {...props} /></div>}
              </div>
            );
          })}
        </>
      )}
      <Lightbox src={zoomSrc} onClose={() => setZoom("")} />
    </div>
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(<App />);
