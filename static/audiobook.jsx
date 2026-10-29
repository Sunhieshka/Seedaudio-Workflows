/* audiobook.jsx — Audiobook Creator (document -> Seed LLM prompts -> Seed Audio 1.5 -> audiobook).
 *
 * Built from the app's own design classes (card, field-label, upload-tile,
 * config-grid, ag-card, btn-primary, btn-action) so it looks like every other tool.
 * Stateless towards the app: renders `state`, writes through `update(fn)`.
 * Backend: content_creator/services/audiobook.py (/api/audiobook/*).
 */
(function () {
  "use strict";
  var useState = React.useState, useEffect = React.useEffect, useRef = React.useRef;

  var STYLES = [
    { value: "single-narrator", label: "Single narrator — performs every voice" },
    { value: "full-cast", label: "Full cast — a voice per character" },
  ];
  // Seed Audio 1.5 voices up to 360 s per call; Seed LLM cuts at natural breaks
  // near these targets and never past ~5 min (headroom for slow narration).
  var LENGTHS = [
    { value: "max", label: "Fewest calls — fill each take to Seed Audio's limit" },
    { value: "long", label: "Long — ~3 min scenes" },
    { value: "scene", label: "Short — ~2 min scenes" },
  ];
  var MUSIC = [
    { value: "none", label: "None (recommended)" },
    { value: "soft", label: "Soft underscore under the voice" },
  ];
  // Preset Seed Audio voices, sent as reference audio "speaker://<voice id>". A preset
  // keeps a speaker's voice identical in every segment, and (unlike an uploaded or
  // generated clip) still allows full 6-minute takes.
  var VOICE_PRESETS = [
    { id: "en_female_wenrouzhishijieshuonv_uranus_bigtts", name: "Megan",
      about: "Gentle, fun older sister · American · shares interesting knowledge in a relaxed way",
      desc: "adult female, gentle and fun older-sister voice with American pronunciation, warm, relaxed and engaging, shares knowledge in an easy conversational way" },
    { id: "en_male_valentino_corey_uranus_bigtts", name: "Clark",
      about: "Mature, dignified uncle · American · steady, powerful professional narration",
      desc: "mature adult male, dignified uncle voice with American pronunciation, steady and powerful professional narration style" },
    { id: "en_male_valentino_uranus_bigtts", name: "Valentino",
      about: "Sunny, cheerful young man full of warmth",
      desc: "young adult male, sunny and cheerful voice full of warmth" },
    { id: "en_female_stokie_uranus_bigtts", name: "Stokie", gender: "Female", accent: "American English", use: "General",
      about: "Trendy, casual and expressive young female · American",
      desc: "young adult female, trendy, casual and expressive voice with American pronunciation" },
    { id: "en_female_dacey_uranus_bigtts", name: "Dacey", gender: "Female", accent: "American English", use: "General",
      about: "Crisp, confident and engaging young female · American",
      desc: "young adult female, crisp, confident and engaging voice with American pronunciation" },
    { id: "en_male_tim_uranus_bigtts", name: "Tim", gender: "Male", accent: "American English", use: "General",
      about: "Clear, versatile and friendly mid-range male · American",
      desc: "adult male, clear, versatile and friendly mid-range voice with American pronunciation" },
    { id: "en_female_nadia_uranus_bigtts", name: "Blair", gender: "Female", accent: "American English", use: "General",
      about: "Young American female · clean and clear, gentle and relaxed delivery",
      desc: "young adult female, clean and clear American voice with a gentle, relaxed way of speaking" },
    { id: "en_male_kevin_uranus_bigtts", name: "Kevin", gender: "Male", accent: "American English", use: "Education",
      about: "Young, clear and friendly American male · fluent, articulate delivery",
      desc: "young adult male, clear and friendly American voice with fluent, articulate delivery" },
    { id: "en_male_jamie_uranus_bigtts", name: "Jamie", gender: "Male", accent: "American English", use: "General",
      about: "Hearty male · sincere and candid, witty and full of energy",
      desc: "adult male, hearty voice, sincere and candid, witty and full of energy" },
    { id: "en_female_hayley_uranus_bigtts", name: "Hayley", gender: "Female", accent: "American English", use: "Education",
      about: "Lively female with strong emotional tension · skilled at storytelling",
      desc: "adult female, lively voice with strong emotional tension, an expressive storyteller" },
  ];
  // Recorded samples of each preset (static/voice-sample-<name>.wav), so a voice can be heard
  // before it is chosen without a Seed Audio call. Voices without one fall back to a generated preview.
  var VOICE_SAMPLES = ["Megan", "Clark", "Valentino", "Stokie", "Dacey", "Tim", "Blair", "Kevin", "Jamie", "Hayley"];
  VOICE_PRESETS.forEach(function (p) {
    if (VOICE_SAMPLES.indexOf(p.name) >= 0) p.sample = "/static/voice-sample-" + p.name.toLowerCase() + ".wav";
  });
  // Shared with the Seed Audio panel's preset voice picker (app.py).
  window.SEED_AUDIO_VOICE_PRESETS = VOICE_PRESETS;

  // Listen-before-you-choose grid of every preset voice. onUse(id) adds a "Use" button per voice;
  // selectedId highlights the current choice. Also used by the Seed Audio panel.
  var GALLERY_TINT = { Female: ["#FCE7F3", "#9D174D"], Male: ["#DBEAFE", "#1E40AF"] };
  function VoiceGallery(props) {
    var playingS = useState("");
    var audioRef = useRef(null);
    useEffect(function () { return function () { if (audioRef.current) audioRef.current.pause(); }; }, []);
    function toggle(p) {
      if (!audioRef.current) {
        audioRef.current = new Audio();
        audioRef.current.onended = function () { playingS[1](""); };
      }
      var a = audioRef.current;
      if (playingS[0] === p.id) { a.pause(); playingS[1](""); return; }
      a.src = p.sample;
      a.play().then(function () { playingS[1](p.id); }).catch(function () { playingS[1](""); });
    }
    return (
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(230px, 1fr))", gap: 10, marginTop: 8 }}>
        {VOICE_PRESETS.map(function (p) {
          var chosen = props.selectedId === p.id;
          var playing = playingS[0] === p.id;
          var gender = p.gender || (/female/.test(p.desc) ? "Female" : "Male");
          var tint = GALLERY_TINT[gender] || ["#EDE9FE", "#5B21B6"];
          var tags = [gender, p.use || "General", "American"].filter(Boolean);
          return (
            <div key={p.id} style={{ display: "flex", flexDirection: "column", gap: 8, padding: 12, borderRadius: 12, background: "var(--surface)",
                                     border: chosen ? "1.5px solid var(--accent-2)" : "0.5px solid var(--border-2)", boxShadow: chosen ? "0 0 0 3px rgba(124,111,227,0.12)" : "none" }}>
              <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                <span aria-hidden="true" style={{ width: 36, height: 36, flex: "none", borderRadius: "50%", background: tint[0], color: tint[1], display: "flex", alignItems: "center", justifyContent: "center", fontWeight: 600 }}>{p.name.charAt(0)}</span>
                <div style={{ minWidth: 0, flex: 1 }}>
                  <div style={{ fontWeight: 600, fontSize: 13.5 }}>{p.name}</div>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginTop: 2 }}>
                    {tags.map(function (t) { return <span key={t} style={{ fontSize: 10.5, padding: "1px 6px", borderRadius: 8, background: "var(--surface-2)", color: "var(--text-secondary)" }}>{t}</span>; })}
                  </div>
                </div>
                {p.sample ? (
                  <button type="button" aria-label={(playing ? "Stop " : "Play ") + p.name} onClick={function () { toggle(p); }}
                          style={{ width: 34, height: 34, flex: "none", borderRadius: "50%", border: 0, cursor: "pointer", display: "flex", alignItems: "center", justifyContent: "center",
                                   background: playing ? "var(--accent-2)" : "rgba(124,111,227,0.10)", color: playing ? "#fff" : "var(--accent-2)" }}>
                    {playing
                      ? <svg viewBox="0 0 24 24" width="12" height="12" fill="currentColor" aria-hidden="true"><rect x="6" y="5" width="4" height="14" rx="1"></rect><rect x="14" y="5" width="4" height="14" rx="1"></rect></svg>
                      : <svg viewBox="0 0 24 24" width="12" height="12" fill="currentColor" aria-hidden="true"><path d="M8 5v14l11-7z"></path></svg>}
                  </button>
                ) : null}
              </div>
              <div style={{ fontSize: 12, color: "var(--text-secondary)", lineHeight: 1.45, minHeight: 34 }}>{p.about}</div>
              {props.onUse ? (
                <button type="button" className="btn-action" disabled={chosen} onClick={function () { props.onUse(p.id); }}
                        style={chosen ? { borderColor: "var(--accent-2)", color: "var(--accent-2)" } : undefined}>
                  {chosen ? "Selected" : (props.useLabel || "Use this voice")}
                </button>
              ) : null}
            </div>
          );
        })}
      </div>
    );
  }
  window.SeedAudioVoiceGallery = VoiceGallery;
  function presetById(id) { return VOICE_PRESETS.filter(function (p) { return p.id === id; })[0] || null; }
  function isPresetUrl(url) { return String(url || "").indexOf("speaker://") === 0; }

  var CUES = [
    { value: "perform", label: "Perform them as sound (SFX, ambience, music)" },
    { value: "read", label: "Read them aloud as written" },
  ];
  // Per-call limits, mirroring content_creator/services/audiobook.py: Seed Audio
  // cuts reference-voice takes off at 240 s and plain TTS at 360 s; caps are the
  // estimated seconds a segment may carry. Estimates: 2.3 words/s, 2 s per cue.
  var CAP_S = { reference: 225, tts: 340 };
  function capFor(lockVoices, pace) {
    var cap = lockVoices ? CAP_S.reference : CAP_S.tts;
    return pace === "slower" ? Math.floor(cap * 0.9) : cap;
  }
  // Document metadata ("Word Count: 1388", "Page 3 of 12") — kept, not spoken.
  var META_RE = /^\s*(word\s*count|character\s*count|char\s*count|page\s+\d+(\s+of\s+\d+)?)\s*[:\-–—]?\s*[\d,.\s]*$/i;
  function isMetaLine(l) { return l.kind === "meta" || (l.kind !== "cue" && (l.text || "").length <= 60 && META_RE.test(l.text || "")); }
  var METADATA = [
    { value: "skip", label: "Keep, but don't read aloud" },
    { value: "read", label: "Read aloud" },
  ];
  function lineSeconds(l) {
    if (isMetaLine(l)) return 0;
    if (l.kind === "cue") return 2;
    var t = l.text || "";
    var cjk = (t.match(/[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]/g) || []).length;
    return cjk > t.length * 0.3 ? cjk / 4.5 : t.split(/\s+/).filter(Boolean).length / 2.3;
  }
  function wordsOf(l) { return l.kind === "cue" ? 0 : (l.text || "").split(/\s+/).filter(Boolean).length; }

  /* Re-pack segments that are not voiced yet so each fits one Seed Audio take for
   * the current voice/pace setting: consecutive unvoiced segments of the same
   * chapter are joined line by line and refilled up to the cap (an over-long single
   * line is split at sentence ends). Lines are moved, never edited, so every word
   * stays. Voiced segments are left untouched. Returns null when nothing changes. */
  function repack(segments, cap, allowMerge) {
    var out = [], changed = false, run = [];
    function flushRun() {
      if (!run.length) return;
      var needs = run.some(function (g) { return (g.est_s || 0) > cap; });
      var total = run.reduce(function (a, g) { return a + (g.est_s || 0); }, 0);
      var minCalls = Math.max(1, Math.ceil(total / cap));
      if (!needs && (!allowMerge || run.length <= minCalls)) { out = out.concat(run); run = []; return; }
      var lines = [];
      run.forEach(function (g) {
        (g.lines || []).forEach(function (l) {
          if (l.kind !== "cue" && lineSeconds(l) > cap) {
            var parts = (l.text || "").split(/(?<=[.!?…。！？]["'”’)]?)\s+/), buf = [];
            parts.forEach(function (p) {
              if (buf.length && lineSeconds({ text: buf.concat([p]).join(" ") }) > cap * 0.9) { lines.push(Object.assign({}, l, { text: buf.join(" ") })); buf = []; }
              buf.push(p);
            });
            if (buf.length) lines.push(Object.assign({}, l, { text: buf.join(" ") }));
          } else lines.push(l);
        });
      });
      var groups = [], cur = [], acc = 0;
      lines.forEach(function (l) {
        var t = lineSeconds(l);
        if (cur.length && acc + t > cap) { groups.push(cur); cur = []; acc = 0; }
        cur.push(l); acc += t;
      });
      if (cur.length) groups.push(cur);
      var first = run[0];
      groups.forEach(function (ls, k) {
        out.push(Object.assign({}, first, {
          id: first.id.replace(/-p\d+$/, "") + "-p" + (k + 1) + Math.random().toString(36).slice(2, 5),
          lines: ls, text: ls.map(function (l) { return l.raw || l.text; }).join("\n\n"),
          words: ls.reduce(function (a, l) { return a + wordsOf(l); }, 0),
          est_s: Math.round(ls.reduce(function (a, l) { return a + lineSeconds(l); }, 0)),
          heading_text: k === 0 ? first.heading_text : "", status: "pending", audioUrl: "", durationMs: null, error: "", customPrompt: "",
        }));
      });
      changed = true;
      run = [];
    }
    segments.forEach(function (g) {
      // Voiced segments, and halves of a take that was cut off (never merged back), stay as they are.
      var fixed = g.status === "done" || g.status === "queued" || g.status === "running" || g.status === "truncated" || g.noMerge;
      if (fixed) { flushRun(); out.push(g); return; }
      if (run.length && run[0].chapter !== g.chapter) flushRun();
      run.push(g);
    });
    flushRun();
    return changed ? out : null;
  }
  var PACE = [
    { value: "normal", label: "Natural" },
    { value: "slower", label: "Slightly slower" },
    { value: "faster", label: "Slightly brisker" },
  ];
  var PAGE = 25;

  function defaultState() {
    return {
      fileName: "", text: "", words: 0, estMinutes: 0, headings: 0,
      options: { style: "single-narrator", length: "max", music: "none", pace: "normal", narrator: "", narratorPreset: "", cues: "perform", metadata: "skip", audioModel: "" },
      analysisJob: null,
      cast: null,
      segments: [],
      voices: {},
      lockVoices: true,
      renderJob: null,
      book: null,
    };
  }

  async function post(path, body) {
    var res = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
    var data = await res.json().catch(function () { return {}; });
    if (!res.ok) throw new Error((data.error && (data.error.message || data.error)) || ("HTTP " + res.status));
    return data;
  }

  function readDataUrl(file) {
    return new Promise(function (resolve, reject) {
      var r = new FileReader();
      r.onload = function () { resolve(String(r.result)); };
      r.onerror = function () { reject(r.error); };
      r.readAsDataURL(file);
    });
  }

  function fmtTime(ms) {
    if (ms == null) return "—";
    var s = Math.round(ms / 1000), h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
    return (h ? h + ":" + String(m).padStart(2, "0") : m) + ":" + String(sec).padStart(2, "0");
  }

  function castPeople(cast) {
    if (!cast) return [];
    var firstLine = "";
    return [{ name: "Narrator", voice: cast.narrator || "", sample: cast.narratorSample || firstLine }]
      .concat((cast.characters || []).map(function (c) { return { name: c.name, voice: c.voice, sample: c.sample }; }));
  }

  function Icon(props) {
    var paths = {
      doc: <React.Fragment><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline><line x1="9" y1="13" x2="15" y2="13"></line><line x1="9" y1="17" x2="13" y2="17"></line></React.Fragment>,
      paste: <React.Fragment><rect x="8" y="2" width="8" height="4" rx="1"></rect><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"></path></React.Fragment>,
      book: <React.Fragment><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"></path><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"></path></React.Fragment>,
    };
    return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">{paths[props.name]}</svg>;
  }

  function AudiobookTool(props) {
    var s = Object.assign(defaultState(), props.state || {});
    s.options = Object.assign(defaultState().options, s.options || {});
    if (!LENGTHS.some(function (o) { return o.value === s.options.length; })) s.options.length = "max";
    if (s.options.cues !== "read") s.options.cues = "perform";
    if (s.options.metadata !== "read") s.options.metadata = "skip";
    var update = props.update;
    function set(patch) {
      update(function (prev) {
        var base = Object.assign(defaultState(), prev || {});
        return Object.assign({}, base, typeof patch === "function" ? patch(base) : patch);
      });
    }
    function setOption(k, v) { set(function (prev) { var o = Object.assign({}, prev.options); o[k] = v; return { options: o }; }); }

    // Models: the LLM is the app-wide choice (/api/system/models); the audio
    // model is per book — blank uses the server's Seed Audio endpoint.
    var llmS = useState(null);
    var audioDefaultS = useState("");
    var customAudioS = useState(false);
    useEffect(function () {
      post("/api/system/capabilities", {}).then(function (d) { llmS[1](d.models || null); }).catch(function () {});
      post("/api/seed-audio/config", {}).then(function (d) { audioDefaultS[1](d.model || ""); }).catch(function () {});
    }, []);
    function chooseLlm(id) {
      post("/api/system/models", { prompt: id }).then(function (d) { llmS[1](d); }).catch(function (e) { alert("Could not change the model: " + e.message); });
    }

    var fileRef = useRef(null);
    var voiceFileRef = useRef(null);
    var voiceTarget = useRef("");
    var errS = useState("");
    var busyS = useState("");
    var pasteS = useState(false);
    var pasteText = useState("");
    var pageS = useState(1);
    var openPrompt = useState({});
    var openText = useState(false);

    // ---------------------------------------------------------- job polling
    var analysisId = s.analysisJob && s.analysisJob.status === "running" ? s.analysisJob.id : "";
    useEffect(function () {
      if (!analysisId) return undefined;
      var write = update, stopped = false;
      async function tick() {
        var job;
        try { job = await post("/api/audiobook/status", { job_id: analysisId }); }
        catch (e) { job = { status: "error", stage: "Failed", error: String(e.message || e) }; }
        if (stopped) return;
        write(function (prev) {
          var base = Object.assign(defaultState(), prev || {});
          var next = { analysisJob: Object.assign({}, base.analysisJob, { status: job.status, stage: job.stage, done: job.done, total: job.total, error: job.error }) };
          if (job.status === "done" && job.result) {
            var cast = job.result.cast;
            // Narrator sample: the first narration sentences, 15-35 words — long enough
            // for a solid voice reference, short enough to stay well under 30 s.
            var picked = [];
            (job.result.segments || []).some(function (seg) {
              if (seg.heading) return false;
              return (seg.lines || []).some(function (l) {
                if (l.speaker !== "Narrator") return false;
                l.text.split(/(?<=[.!?])\s+/).some(function (sent) {
                  if (picked.join(" ").split(" ").length + sent.split(" ").length > 35 && picked.length) return true;
                  picked.push(sent);
                  return picked.join(" ").split(" ").length >= 15;
                });
                return picked.join(" ").split(" ").length >= 15;
              });
            });
            cast.narratorSample = picked.join(" ").slice(0, 260);
            next.cast = cast;
            next.segments = job.result.segments.map(function (seg) { return Object.assign({}, seg, { status: "pending" }); });
            next.coverage = job.result.coverage || null;
            // Preset voices chosen for this project stay with their speakers across
            // re-analysis; the Narration step's narrator voice is applied here.
            var keep = {}, names = ["Narrator"].concat((cast.characters || []).map(function (c) { return c.name; }));
            Object.keys(base.voices || {}).forEach(function (k) {
              var v = base.voices[k];
              if (v && isPresetUrl(v.url) && names.indexOf(k) >= 0) keep[k] = v;
            });
            var np = presetById(base.options.narratorPreset);
            if (np) {
              keep.Narrator = { status: "done", url: "speaker://" + np.id, source: "preset", presetId: np.id };
              cast.narrator = np.desc;
            } else if (keep.Narrator && presetById(keep.Narrator.presetId)) {
              cast.narrator = presetById(keep.Narrator.presetId).desc;
            }
            (cast.characters || []).forEach(function (c) {
              var kv = keep[c.name], pr = kv && presetById(kv.presetId);
              if (pr) c.voice = pr.desc;
            });
            next.voices = keep;
            next.book = null;
          }
          return Object.assign({}, base, next);
        });
      }
      tick();
      var t = setInterval(tick, 2000);
      return function () { stopped = true; clearInterval(t); };
    }, [analysisId]);

    var renderId = s.renderJob && s.renderJob.status === "running" ? s.renderJob.id : "";
    useEffect(function () {
      if (!renderId) return undefined;
      var write = update, stopped = false;
      async function tick() {
        var job;
        try { job = await post("/api/audiobook/status", { job_id: renderId }); }
        catch (e) { job = { status: "error", error: String(e.message || e), items: {} }; }
        if (stopped) return;
        write(function (prev) {
          var base = Object.assign(defaultState(), prev || {});
          var items = job.items || {};
          var segments = base.segments.map(function (seg) {
            var it = items[seg.id];
            if (!it) return job.status === "running" ? seg : (seg.status === "queued" ? Object.assign({}, seg, { status: job.status === "error" ? "error" : "pending", error: job.error || "" }) : seg);
            return Object.assign({}, seg, { status: it.status, audioUrl: it.url || seg.audioUrl, durationMs: it.duration_ms || seg.durationMs, error: it.error || "" });
          });
          return Object.assign({}, base, { segments: segments, renderJob: Object.assign({}, base.renderJob, { status: job.status, done: job.done, total: job.total, error: job.error }) });
        });
      }
      tick();
      var t = setInterval(tick, 3000);
      return function () { stopped = true; clearInterval(t); };
    }, [renderId]);

    // ------------------------------------------------------------ actions
    async function loadDocument(file) {
      if (!file) return;
      errS[1](""); busyS[1]("extract");
      try {
        var data = await post("/api/audiobook/extract", { filename: file.name, data_url: await readDataUrl(file) });
        set({ fileName: file.name, text: data.text, words: data.words, estMinutes: data.est_minutes, headings: data.headings,
              analysisJob: null, cast: null, segments: [], voices: {}, renderJob: null, book: null });
      } catch (e) { errS[1](e.message); }
      finally { busyS[1](""); }
    }

    async function usePastedText() {
      var text = pasteText[0].trim();
      if (!text) return;
      var blob = new Blob([text], { type: "text/plain" });
      var file = new File([blob], "pasted-text.txt", { type: "text/plain" });
      await loadDocument(file);
      pasteS[1](false); pasteText[1]("");
    }

    async function analyze() {
      errS[1]("");
      try {
        // Analysis clears the voice samples, so new segments start at the no-reference
        // limit; creating samples later re-packs the unvoiced ones to the 4-minute limit.
        var np = presetById(s.options.narratorPreset);
        var data = await post("/api/audiobook/analyze", { text: s.text, options: Object.assign({}, s.options, { lockVoices: false }, np ? { narrator: np.desc } : {}) });
        set({ analysisJob: { id: data.job_id, status: "running", stage: "Starting", done: 0, total: 0 } });
      } catch (e) { errS[1](e.message); }
    }

    function setCast(patch) { set(function (prev) { return { cast: Object.assign({}, prev.cast, patch) }; }); }
    function setPerson(name, field, value) {
      set(function (prev) {
        var cast = Object.assign({}, prev.cast);
        if (name === "Narrator") cast[field === "voice" ? "narrator" : "narratorSample"] = value;
        else cast.characters = cast.characters.map(function (c) { if (c.name !== name) return c; var n = Object.assign({}, c); n[field] = value; return n; });
        return { cast: cast };
      });
    }
    function setVoice(name, patch) {
      set(function (prev) { var v = Object.assign({}, prev.voices); v[name] = Object.assign({}, v[name], patch); return { voices: v }; });
    }

    async function makeSample(person) {
      if (!person.voice.trim() || !person.sample.trim()) { setVoice(person.name, { status: "error", error: "Needs a voice description and a sample line." }); return; }
      var write = update;
      setVoice(person.name, { status: "running", error: "" });
      try {
        var res = await post("/api/audiobook/voice-sample", { name: person.name, voice: person.voice, line: person.sample, language: (s.cast && s.cast.language) || "", model: s.options.audioModel || "" });
        write(function (prev) { var base = Object.assign(defaultState(), prev || {}); var v = Object.assign({}, base.voices); v[person.name] = { status: "done", url: res.url, source: "generated", durationMs: res.duration_ms }; return Object.assign({}, base, { voices: v }); });
      } catch (e) {
        write(function (prev) { var base = Object.assign(defaultState(), prev || {}); var v = Object.assign({}, base.voices); v[person.name] = Object.assign({}, v[person.name], { status: "error", error: e.message }); return Object.assign({}, base, { voices: v }); });
      }
    }

    async function makeAllSamples() {
      var people = castPeople(s.cast).filter(function (p) { var v = s.voices[p.name]; return !(v && v.url) && !(v && v.status === "running"); });
      // Two at a time: the Seed Audio early-access limit.
      for (var i = 0; i < people.length; i += 2) await Promise.all(people.slice(i, i + 2).map(makeSample));
    }

    async function uploadVoice(file) {
      var name = voiceTarget.current;
      if (!file || !name) return;
      var ext = file.name.split(".").pop().toLowerCase();
      if (["wav", "mp3"].indexOf(ext) < 0) { setVoice(name, { status: "error", error: "Voice clips must be WAV or MP3 (2–30 s)." }); return; }
      setVoice(name, { status: "running", error: "" });
      try {
        var res = await post("/api/seed-audio/upload", { audio_data_url: await readDataUrl(file), filename: file.name });
        setVoice(name, { status: "done", url: res.file_url, source: "upload" });
      } catch (e) { setVoice(name, { status: "error", error: e.message }); }
    }

    function lockedVoices() {
      var out = {};
      Object.keys(s.voices).forEach(function (k) {
        var v = s.voices[k];
        if (!v || !v.url) return;
        if (isPresetUrl(v.url) || s.lockVoices) out[k] = v.url;   // a chosen preset is always used
      });
      return out;
    }

    // Picking the narrator voice in step 2 after analysis applies straight away.
    var narratorPresetId = s.options.narratorPreset || "";
    useEffect(function () {
      if (!s.cast) return;
      var cur = (s.voices || {}).Narrator;
      if (narratorPresetId) {
        if (!cur || cur.presetId !== narratorPresetId) choosePreset({ name: "Narrator" }, narratorPresetId);
      } else if (cur && cur.source === "preset" && presetById(cur.presetId)) {
        choosePreset({ name: "Narrator" }, "");
      }
    }, [narratorPresetId, !!s.cast]);

    function choosePreset(person, presetId) {
      if (person.name === "Narrator" && (s.options.narratorPreset || "") !== (presetId || "")) setOption("narratorPreset", presetId || "");
      if (!presetId) {
        set(function (prev) { var v = Object.assign({}, prev.voices); if (v[person.name] && isPresetUrl(v[person.name].url)) delete v[person.name]; return { voices: v }; });
        return;
      }
      var pr = presetById(presetId);
      setVoice(person.name, { status: "done", url: "speaker://" + presetId, source: "preset", presetId: presetId, preview: (pr && pr.sample) || "", error: "" });
      if (pr) setPerson(person.name, "voice", pr.desc);
    }

    async function previewPreset(person) {
      var v = s.voices[person.name] || {};
      if (!isPresetUrl(v.url)) return;
      var pr = presetById(v.presetId);
      if (pr && pr.sample) { setVoice(person.name, { preview: pr.sample, previewing: false, error: "" }); return; }
      var write = update;
      setVoice(person.name, { previewing: true, error: "" });
      try {
        var res = await post("/api/audiobook/voice-sample", { preset: v.presetId || v.url, line: person.sample || "Hello! This is how I will narrate your audiobook.", language: (s.cast && s.cast.language) || "", model: s.options.audioModel || "" });
        write(function (prev) { var base = Object.assign(defaultState(), prev || {}); var vv = Object.assign({}, base.voices); vv[person.name] = Object.assign({}, vv[person.name], { preview: res.url, previewing: false }); return Object.assign({}, base, { voices: vv }); });
      } catch (e) {
        write(function (prev) { var base = Object.assign(defaultState(), prev || {}); var vv = Object.assign({}, base.voices); vv[person.name] = Object.assign({}, vv[person.name], { previewing: false, error: e.message }); return Object.assign({}, base, { voices: vv }); });
      }
    }

    async function promptsFor(segs) {
      var data = await post("/api/audiobook/prompts", {
        book: { cast: s.cast, options: s.options }, voices: lockedVoices(),
        segments: segs.map(function (g) { return { id: g.id, chapter: g.chapter, heading: g.heading, lines: g.lines }; }),
      });
      return data.prompts || {};
    }

    async function render(segs) {
      errS[1]("");
      if (!segs.length) return;
      try {
        var prompts = await promptsFor(segs);
        var items = segs.map(function (g) {
          var p = prompts[g.id] || {};
          return { id: g.id, prompt: g.customPrompt || p.prompt, refs: p.refs || [] };
        });
        var speechRate = s.options.pace === "slower" ? -10 : s.options.pace === "faster" ? 10 : 0;
        var data = await post("/api/audiobook/render", { items: items, audio: { format: "wav", speech_rate: speechRate, model: s.options.audioModel || "" } });
        var ids = {};
        segs.forEach(function (g) { ids[g.id] = true; });
        set(function (prev) {
          return {
            renderJob: { id: data.job_id, status: "running", done: 0, total: items.length },
            book: null,
            segments: prev.segments.map(function (g) { return ids[g.id] ? Object.assign({}, g, { status: "queued", error: "" }) : g; }),
          };
        });
      } catch (e) { errS[1](e.message); }
    }

    async function togglePrompt(seg) {
      var cur = openPrompt[0][seg.id];
      if (cur) { var n = Object.assign({}, openPrompt[0]); delete n[seg.id]; openPrompt[1](n); return; }
      try {
        var p = (await promptsFor([seg]))[seg.id] || {};
        var m = Object.assign({}, openPrompt[0]); m[seg.id] = p.prompt || p.error || ""; openPrompt[1](m);
      } catch (e) { errS[1](e.message); }
    }

    // A take that came back cut off at Seed Audio's limit is split in two at the
    // line nearest its middle (a sentence boundary if it is one long line), and
    // both halves are rendered — no manual re-analysis needed.
    function splitSegment(g) {
      var lines = g.lines || [];
      var words = function (l) { return l.kind === "cue" ? 0 : (l.text || "").split(/\s+/).length; };
      var total = lines.reduce(function (a, l) { return a + words(l); }, 0);
      var first, second;
      if (lines.length >= 2) {
        var best = 1, bestDiff = Infinity, acc = 0;
        for (var k = 1; k < lines.length; k++) {
          acc += words(lines[k - 1]);
          var diff = Math.abs(acc - total / 2);
          if (diff < bestDiff) { bestDiff = diff; best = k; }
        }
        first = lines.slice(0, best); second = lines.slice(best);
      } else {
        var only = lines[0] || { speaker: "Narrator", delivery: "", text: g.text };
        var sents = only.text.split(/(?<=[.!?])\s+/);
        if (sents.length < 2) return null;
        var half = Math.ceil(sents.length / 2);
        first = [Object.assign({}, only, { text: sents.slice(0, half).join(" ") })];
        second = [Object.assign({}, only, { text: sents.slice(half).join(" ") })];
      }
      function make(part, suffix, keepHeading) {
        var text = part.map(function (l) { return l.text; }).join("\n\n");
        var w = part.reduce(function (a, l) { return a + words(l); }, 0);
        return Object.assign({}, g, {
          id: g.id + suffix, lines: part, text: text, words: w, est_s: Math.round((g.est_s || 0) * (w / Math.max(1, total))),
          heading_text: keepHeading ? g.heading_text : "", status: "pending", audioUrl: "", durationMs: null, error: "", customPrompt: "",
          noMerge: true,
        });
      }
      return [make(first, "a", true), make(second, "b", false)];
    }

    var truncatedIds = s.segments.filter(function (g) { return g.status === "truncated"; }).map(function (g) { return g.id; }).join(",");
    useEffect(function () {
      if (!truncatedIds || (s.renderJob && s.renderJob.status === "running")) return;
      var halves = [], unsplittable = {};
      s.segments.forEach(function (g) {
        if (g.status !== "truncated") return;
        var parts = splitSegment(g);
        if (parts) halves = halves.concat(parts); else unsplittable[g.id] = true;
      });
      set(function (prev) {
        return { segments: prev.segments.reduce(function (acc, g) {
          if (g.status !== "truncated") return acc.concat([g]);
          if (unsplittable[g.id]) return acc.concat([Object.assign({}, g, { status: "error", error: "Cut off at Seed Audio's limit and too short to split — shorten its prompt." })]);
          var parts = halves.filter(function (h) { return h.id === g.id + "a" || h.id === g.id + "b"; });
          return acc.concat(parts);
        }, []) };
      });
      if (halves.length) render(halves);
    }, [truncatedIds, s.renderJob && s.renderJob.status]);

    // Keep every unvoiced segment within one take for the current Lock voices /
    // pace setting — re-packed automatically, no re-analysis.
    // The 240 s limit applies only when reference voices are actually sent, i.e. Lock
    // voices is on AND at least one voice sample exists; otherwise takes run to 360 s.
    var refsInUse = !!s.lockVoices && Object.keys(s.voices || {}).some(function (k) { var v = s.voices[k]; return v && v.url && !isPresetUrl(v.url); });
    var packCap = capFor(refsInUse, s.options.pace);
    var analysisBusy = s.analysisJob && s.analysisJob.status === "running";
    var renderBusy = s.renderJob && s.renderJob.status === "running";
    useEffect(function () {
      if (analysisBusy || renderBusy || !s.segments.length) return;
      var next = repack(s.segments, packCap, s.options.length === "max");
      if (next) set(function () { return { segments: next, book: null }; });
    }, [packCap, analysisBusy, renderBusy, s.segments.length, s.options.length]);

    function setSegment(id, patch) {
      set(function (prev) { return { segments: prev.segments.map(function (g) { return g.id === id ? Object.assign({}, g, patch) : g; }) }; });
    }

    async function assemble() {
      errS[1](""); busyS[1]("assemble");
      var write = update;
      try {
        var parts = s.segments.filter(function (g) { return g.status === "done" && g.audioUrl; })
          .map(function (g) { return { url: g.audioUrl, chapter: g.chapter || "" }; });
        var res = await post("/api/audiobook/assemble", { parts: parts, title: (s.cast && s.cast.title) || s.fileName || "Audiobook" });
        write(function (prev) { return Object.assign({}, Object.assign(defaultState(), prev || {}), { book: Object.assign({ createdAt: Date.now() }, res) }); });
      } catch (e) { errS[1](e.message); }
      finally { busyS[1](""); }
    }

    // ------------------------------------------------------------- derived
    var analysis = s.analysisJob;
    var analyzing = analysis && analysis.status === "running";
    var rendering = s.renderJob && s.renderJob.status === "running";
    var segs = s.segments;
    var doneCount = segs.filter(function (g) { return g.status === "done"; }).length;
    var failed = segs.filter(function (g) { return g.status === "error"; });
    var pending = segs.filter(function (g) { return g.status === "pending" || g.status === "error"; });
    var verified = segs.filter(function (g) { return g.verified; }).length;
    var estTotal = segs.reduce(function (a, g) { return a + (g.est_s || 0); }, 0);
    var totalMs = segs.reduce(function (a, g) { return a + (g.durationMs || 0); }, 0);
    var people = castPeople(s.cast);
    var voicesReady = people.filter(function (p) { return s.voices[p.name] && s.voices[p.name].url; }).length;
    var shown = segs.slice(0, pageS[0] * PAGE);
    // Segment numbers count passages only; headings are labelled as such.
    var segNumber = {}, n = 0;
    segs.forEach(function (g) { segNumber[g.id] = ++n; });
    var speakerColours = {};
    people.forEach(function (p, i) { speakerColours[p.name] = ["#2563EB", "#7C6FE3", "#DB2777", "#16A34A", "#D97706", "#0891B2", "#9333EA", "#DC2626"][i % 8]; });

    return (
      <div className="card">
        <div className="video-hint" style={{ marginTop: 0, marginBottom: 14 }}>
          Upload a book or document. Seed LLM casts the voices and turns every passage into a Seed Audio 1.5 prompt — the wording is kept exactly — then the whole book is voiced and assembled into one audiobook with chapters.
        </div>

        {/* ------------------------------------------------------ 1. document */}
        <div className="field-label">1 · Document</div>
        <input ref={fileRef} type="file" accept=".pdf,.docx,.epub,.txt,.md,.markdown,.html,.htm" style={{ display: "none" }}
               onChange={function (e) { loadDocument(e.target.files && e.target.files[0]); e.target.value = ""; }} />
        <div className="tile-grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 220px), 1fr))" }}>
          <div className="upload-tile" role="button" tabIndex="0" onClick={function () { fileRef.current.click(); }}
               onKeyDown={function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fileRef.current.click(); } }}>
            <Icon name="doc" />
            <div className="tile-title">{busyS[0] === "extract" ? "Reading…" : "Upload document"}</div>
            <div className="tile-meta">PDF · DOCX · EPUB · TXT · MD · up to ~100k words</div>
          </div>
          <div className="upload-tile" role="button" tabIndex="0" onClick={function () { pasteS[1](!pasteS[0]); }}
               onKeyDown={function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pasteS[1](!pasteS[0]); } }}>
            <Icon name="paste" />
            <div className="tile-title">Paste text</div>
            <div className="tile-meta">Chapters from headings</div>
          </div>
        </div>
        {s.fileName || s.words ? (
          <div className="video-hint">
            <strong style={{ fontWeight: 500, color: "var(--text-primary)" }}>{s.fileName || "Pasted text"}</strong>
            {s.words ? " · " + s.words.toLocaleString() + " words · ~" + s.estMinutes + " min · " + s.headings + " chapter heading" + (s.headings === 1 ? "" : "s") : ""}
          </div>
        ) : null}
        {pasteS[0] ? (
          <div style={{ marginTop: 10 }}>
            <textarea className="video-input" rows="8" placeholder={"Paste the text. Put chapter titles on their own line, e.g.\n\nChapter One: The Storm\n\nThe storm came in from the west…"}
                      value={pasteText[0]} onChange={function (e) { pasteText[1](e.target.value); }} />
            <div className="btn-row" style={{ marginTop: 8 }}>
              <button className="btn-action btn-inline" disabled={!pasteText[0].trim()} onClick={usePastedText}>Use this text</button>
            </div>
          </div>
        ) : null}
        {s.text ? (
          <div className="ab-doc">
            <div className="ab-doc-head">
              <span className="ab-doc-title"><Icon name="doc" />{s.fileName || "Pasted text"}</span>
              <span className="ab-doc-meta">{(s.words || 0).toLocaleString()} words · {s.headings} chapter{s.headings === 1 ? "" : "s"}</span>
              <button className="btn-link" onClick={function () { openText[1](!openText[0]); }}>{openText[0] ? "Collapse" : "Expand"}</button>
            </div>
            <div className={"ab-doc-body" + (openText[0] ? " is-open" : "")}>
              {s.text.slice(0, openText[0] ? 60000 : 8000).split(/\n{2,}/).map(function (para, i) {
                var t = para.trim();
                if (!t) return null;
                var heading = t.length < 90 && !/[.!?…"”]$/.test(t) && (/^(chapter|part|book|prologue|epilogue|section)\b/i.test(t) || t === t.toUpperCase());
                return heading ? <h4 key={i} className="ab-doc-h">{t}</h4> : <p key={i} className="ab-doc-p">{t}</p>;
              })}
              {s.text.length > (openText[0] ? 60000 : 8000) ? <p className="ab-doc-more">{openText[0] ? "Preview shows the first 60,000 characters." : "…"}</p> : null}
            </div>
          </div>
        ) : null}

        {/* ------------------------------------------------------ 2. narration */}
        <hr className="divider" />
        <div className="field-label">2 · Narration</div>
        <div className="config-grid" style={{ marginTop: 0, marginBottom: 12 }}>
          <div>
            <div className="field-label">LLM <span style={{ textTransform: "none", letterSpacing: 0, fontWeight: 400 }}>— casting and prompts · app-wide</span></div>
            <select className="select-input" value={llmS[0] && llmS[0].prompt ? llmS[0].prompt.id : ""} disabled={!llmS[0]} onChange={function (e) { chooseLlm(e.target.value); }}>
              {((llmS[0] && llmS[0].options && llmS[0].options.prompt) || []).map(function (o) { return <option key={o.id} value={o.id}>{o.label} · {o.note}</option>; })}
            </select>
          </div>
          <div>
            <div className="field-label">Audio model <span style={{ textTransform: "none", letterSpacing: 0, fontWeight: 400 }}>— voices and narration</span></div>
            <select className="select-input" value={customAudioS[0] || (s.options.audioModel && s.options.audioModel !== audioDefaultS[0]) ? "custom" : ""}
                    onChange={function (e) { var custom = e.target.value === "custom"; customAudioS[1](custom); if (!custom) setOption("audioModel", ""); }}>
              <option value="">Seed Audio 1.5{audioDefaultS[0] ? " · " + audioDefaultS[0] : ""} (server default)</option>
              <option value="custom">Custom endpoint ID…</option>
            </select>
            {customAudioS[0] || (s.options.audioModel && s.options.audioModel !== audioDefaultS[0]) ? (
              <input className="select-input" style={{ marginTop: 6 }} placeholder="ep-…" value={s.options.audioModel}
                     onChange={function (e) { setOption("audioModel", e.target.value.trim()); }} />
            ) : null}
          </div>
        </div>
        <div className="config-grid" style={{ marginTop: 0 }}>
          <div>
            <div className="field-label">Voices</div>
            <select className="select-input" value={s.options.style} onChange={function (e) { setOption("style", e.target.value); }}>
              {STYLES.map(function (o) { return <option key={o.value} value={o.value}>{o.label}</option>; })}
            </select>
          </div>
          <div>
            <div className="field-label">Segment length</div>
            <select className="select-input" value={s.options.length} onChange={function (e) { setOption("length", e.target.value); }}>
              {LENGTHS.map(function (o) { return <option key={o.value} value={o.value}>{o.label}</option>; })}
            </select>
          </div>
          <div>
            <div className="field-label">Background music</div>
            <select className="select-input" value={s.options.music} onChange={function (e) { setOption("music", e.target.value); }}>
              {MUSIC.map(function (o) { return <option key={o.value} value={o.value}>{o.label}</option>; })}
            </select>
          </div>
          <div>
            <div className="field-label">Pace</div>
            <select className="select-input" value={s.options.pace} onChange={function (e) { setOption("pace", e.target.value); }}>
              {PACE.map(function (o) { return <option key={o.value} value={o.value}>{o.label}</option>; })}
            </select>
          </div>
          <div>
            <div className="field-label">Sound cues in the script</div>
            <select className="select-input" value={s.options.cues} onChange={function (e) { setOption("cues", e.target.value); }}>
              {CUES.map(function (o) { return <option key={o.value} value={o.value}>{o.label}</option>; })}
            </select>
          </div>
          <div>
            <div className="field-label">Document info (word count, page numbers)</div>
            <select className="select-input" value={s.options.metadata} onChange={function (e) { setOption("metadata", e.target.value); }}>
              {METADATA.map(function (o) { return <option key={o.value} value={o.value}>{o.label}</option>; })}
            </select>
          </div>
          <div className="video-hint" style={{ gridColumn: "1 / -1", marginTop: 0 }}>
            Every word of the document is kept in the segments. Sound-cue lines (“SFX: …”, “AMBIENT SFX: …”, “MUSIC: …”) are performed as sound or read aloud; document info such as “Word Count: 1388” or “Page 3” is kept but not spoken unless you choose to read it.
          </div>
          <div style={{ gridColumn: "1 / -1" }}>
            <label className="sheet-chip checkbox-chip" data-checked={s.lockVoices ? "true" : "false"}>
              <input type="checkbox" checked={!!s.lockVoices} onChange={function () { set(function (prev) { return { lockVoices: !prev.lockVoices }; }); }} />
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M20 6 9 17l-5-5"></path></svg>
              Lock voices with samples (recommended)
            </label>
            <div className="video-hint">
              {s.lockVoices
                ? "Each speaker gets a voice sample that every segment references, so voices stay identical across the book. Seed Audio cuts reference-voice takes off at 4 minutes, so once samples exist each call carries up to ~3¾ min (up to ~5¾ min until then)."
                : "Speakers are voiced from their descriptions only (voices can drift between segments). Without references Seed Audio voices up to 6 minutes per call, so each call carries up to ~5¾ min."}
            </div>
          </div>
          <div style={{ gridColumn: "1 / -1" }}>
            <div className="field-label">Narrator voice</div>
            <select className="select-input" value={s.options.narratorPreset || ""} onChange={function (e) { setOption("narratorPreset", e.target.value); }}>
              <option value="">Seed LLM casts a voice (from the description below)</option>
              {VOICE_PRESETS.map(function (pr) { return <option key={pr.id} value={pr.id}>{pr.name} — {pr.about}</option>; })}
              {s.options.narratorPreset && !presetById(s.options.narratorPreset) ? <option value={s.options.narratorPreset}>Custom voice ID · {s.options.narratorPreset}</option> : null}
            </select>
            <div className="video-hint">A preset voice is used for the narrator in every segment of this project, and still allows full 6-minute takes. Characters can get their own preset voice in step 3.</div>
            <details style={{ marginTop: 6 }}>
              <summary className="video-hint" style={{ cursor: "pointer" }}>Listen to the preset voices ({VOICE_PRESETS.length})</summary>
              <VoiceGallery selectedId={s.options.narratorPreset || ""} useLabel="Use for narrator" onUse={function (id) { setOption("narratorPreset", id); }} />
            </details>
          </div>
          <div style={{ gridColumn: "1 / -1", display: s.options.narratorPreset ? "none" : "block" }}>
            <div className="field-label">Narrator description <span className="ab-optional">(optional — otherwise Seed LLM casts one)</span></div>
            <input className="video-input sa-input" value={s.options.narrator} onChange={function (e) { setOption("narrator", e.target.value); }}
                   placeholder="e.g. 30s-40s male, grounded baritone, warm textured tone, calm and immersive, slow measured pace" />
          </div>
        </div>
        <div className="video-hint">{s.options.length === "max"
          ? "Fewest calls: each segment is filled right up to what Seed Audio voices in one take (4 min with locked voices, 6 min without); only the remainder goes into the next segment, and a cut moves to just before a sound cue when that costs no extra call. A take that still comes back cut off is split in two and re-rendered automatically."
          : "Seed LLM chooses where each segment ends — at scene changes, finished conversations or topic shifts — keeping every segment within one Seed Audio take."} Length, voices and pace apply when you analyze; music and sound cues apply to every segment you generate.</div>
        <div className="btn-row">
          <button className="btn-primary" disabled={!s.text || analyzing} onClick={analyze}>
            {analyzing ? (analysis.stage || "Analyzing…") + (analysis.total ? " · " + (analysis.done || 0) + "/" + analysis.total : "") : s.segments.length ? "Re-analyze with Seed LLM" : "Analyze with Seed LLM"}
          </button>
        </div>
        {analysis && analysis.status === "error" ? <div className="error-box">{analysis.error}</div> : null}

        {/* ------------------------------------------------------ 3. voices */}
        {s.cast ? (
          <React.Fragment>
            <hr className="divider" />
            <div className="ab-head">
              <div className="field-label" style={{ margin: 0 }}>3 · Cast &amp; voices</div>
              <button className="btn-action btn-inline" disabled={voicesReady === people.length} onClick={makeAllSamples}>Generate all voice samples</button>
            </div>
            <div className="config-grid" style={{ marginTop: 8 }}>
              <div>
                <div className="field-label">Title</div>
                <input className="video-input sa-input" value={s.cast.title || ""} onChange={function (e) { setCast({ title: e.target.value }); }} />
              </div>
              <div>
                <div className="field-label">Language</div>
                <input className="video-input sa-input" value={s.cast.language || ""} onChange={function (e) { setCast({ language: e.target.value }); }} />
              </div>
            </div>
            <div className="video-hint">{s.lockVoices ? "Voices are locked: every segment uses these samples as @Audio references, so the voices stay the same throughout. " : "Voices are not locked: speakers are voiced from their descriptions, which can drift between segments. "}{voicesReady}/{people.length} voices ready.</div>
            <input ref={voiceFileRef} type="file" accept=".wav,.mp3,audio/wav,audio/mpeg" style={{ display: "none" }}
                   onChange={function (e) { uploadVoice(e.target.files && e.target.files[0]); e.target.value = ""; }} />
            <div style={{ display: "grid", gap: 10, marginTop: 10 }}>
              {people.map(function (p) {
                var v = s.voices[p.name] || {};
                return (
                  <div key={p.name} className="ag-card" style={{ marginBottom: 0 }}>
                    <div className="ag-header">
                      <div>
                        <div className="ag-title"><span className="ab-dot" style={{ background: speakerColours[p.name] }} />{p.name}</div>
                        <div className="ag-count">{isPresetUrl(v.url)
                          ? "Preset voice · " + ((presetById(v.presetId) || {}).name || v.presetId) + " — used in every segment"
                          : v.status === "running" ? "Creating voice…" : v.url ? (v.source === "upload" ? "Uploaded voice" : "Voice sample ready") + (v.durationMs ? " · " + (v.durationMs / 1000).toFixed(1) + " s" + (v.durationMs < 4000 ? " — short; a longer line gives a steadier voice" : "") : "") : "No sample yet"}</div>
                      </div>
                      <div className="ab-actions">
                        {isPresetUrl(v.url) ? (
                          <button className="btn-action btn-inline" disabled={v.previewing} onClick={function () { previewPreset(p); }}>{v.previewing ? "Previewing…" : "Preview voice"}</button>
                        ) : (
                          <React.Fragment>
                            <button className="btn-action btn-inline" disabled={v.status === "running"} onClick={function () { makeSample(p); }}>{v.url ? "Regenerate" : "Generate sample"}</button>
                            <button className="btn-action btn-inline" disabled={v.status === "running"} onClick={function () { voiceTarget.current = p.name; voiceFileRef.current.click(); }}>Upload voice</button>
                          </React.Fragment>
                        )}
                      </div>
                    </div>
                    <div className="ag-body">
                      <div className="field-label">Voice</div>
                      <select className="select-input" value={isPresetUrl(v.url) ? (presetById(v.presetId) ? v.presetId : "__other") : ""}
                              onChange={function (e) {
                                if (e.target.value === "__other") {
                                  var id = window.prompt("Seed Audio voice ID (e.g. en_female_…_uranus_bigtts):", v.presetId || "");
                                  if (id && id.trim()) choosePreset(p, id.trim().replace("speaker://", ""));
                                } else choosePreset(p, e.target.value);
                              }}>
                        <option value="">From description / voice sample</option>
                        {VOICE_PRESETS.map(function (pr) { return <option key={pr.id} value={pr.id}>{pr.name} — {pr.about}</option>; })}
                        <option value="__other">{isPresetUrl(v.url) && !presetById(v.presetId) ? "Custom voice ID · " + v.presetId : "Other voice ID…"}</option>
                      </select>
                      <div className="field-label" style={{ marginTop: 8 }}>Voice description</div>
                      <input className="video-input sa-input" value={p.voice} onChange={function (e) { setPerson(p.name, "voice", e.target.value); }} />
                      <div className="field-label" style={{ marginTop: 8 }}>Sample line</div>
                      <input className="video-input sa-input" value={p.sample || ""} onChange={function (e) { setPerson(p.name, "sample", e.target.value); }} placeholder="A line for the voice sample (taken from the book)" />
                      {v.url && !isPresetUrl(v.url) ? <audio controls src={v.url} style={{ width: "100%", marginTop: 8 }} /> : null}
                      {isPresetUrl(v.url) && v.preview ? <audio controls src={v.preview} style={{ width: "100%", marginTop: 8 }} /> : null}
                      {v.status === "error" ? <div className="error-box">{v.error}</div> : null}
                    </div>
                  </div>
                );
              })}
            </div>

            {/* -------------------------------------------------- 4. segments */}
            <hr className="divider" />
            <div className="ab-head">
              <div className="field-label" style={{ margin: 0 }}>4 · Segments</div>
              <div className="video-hint" style={{ margin: 0 }}>
                {segs.length} segments · ~{fmtTime(estTotal * 1000)} estimated · {verified}/{segs.length} speaker-labelled · {doneCount}/{segs.length} voiced{totalMs ? " · " + fmtTime(totalMs) + " so far" : ""}
              </div>
            </div>
            {s.coverage ? (
              s.coverage.ok
                ? <div className="video-hint ab-ok">✓ All {Number(s.coverage.doc_words).toLocaleString()} words of the document are in these segments, in order — nothing added, nothing left out.</div>
                : <div className="error-box">Word check failed: {Number(s.coverage.covered_words).toLocaleString()} of {Number(s.coverage.doc_words).toLocaleString()} words are in the segments. Missing: {(s.coverage.missing || []).join(" … ")}</div>
            ) : null}
            {s.lockVoices && !refsInUse ? (
              <div className="video-hint">Lock voices is on, but there are no voice samples yet — so nothing is referenced and each segment can use Seed Audio's full 6-minute take. Create samples above to lock the voices; unvoiced segments then re-pack to 4-minute takes automatically.</div>
            ) : null}
            {s.lockVoices && voicesReady < people.length ? (
              <div className="video-hint">Tip: create the voice samples first so the whole book uses the same voices.</div>
            ) : null}
            <div className="btn-row">
              <button className="btn-primary" disabled={rendering || !pending.length} onClick={function () { render(pending); }}>
                {rendering ? "Generating audio · " + (s.renderJob.done || 0) + "/" + (s.renderJob.total || 0)
                  : doneCount && pending.length ? "Generate remaining " + pending.length + " segment" + (pending.length === 1 ? "" : "s")
                  : doneCount ? "All segments voiced" : "Generate audio for all " + segs.length + " segments"}
              </button>
            </div>
            <div className="video-hint">Each segment is one Seed Audio call (up to 360 s of audio); two run at a time (the early-access limit), roughly {Math.max(1, Math.round(estTotal * 0.8 / 2 / 60))}–{Math.max(2, Math.round(estTotal * 1.6 / 2 / 60))} min for this book. You can switch tools or reload; progress is kept.</div>
            {s.renderJob && s.renderJob.error && !rendering ? <div className="error-box">{s.renderJob.error}</div> : null}
            {failed.length && !rendering ? <div className="error-box">{failed.length} segment{failed.length === 1 ? "" : "s"} failed — the button above retries them. First error: {failed[0].error}</div> : null}

            <div style={{ display: "grid", gap: 8, marginTop: 12 }}>
              {shown.map(function (g, i) {
                var prev = i > 0 ? shown[i - 1] : null;
                var newChapter = g.chapter && (!prev || prev.chapter !== g.chapter);
                return (
                  <React.Fragment key={g.id}>
                    {newChapter ? <div className="ab-chapter">{g.chapter}</div> : null}
                    <div className="ag-card" style={{ marginBottom: 0 }}>
                      <div className="ag-header">
                        <div>
                          <div className="ag-title">Segment {segNumber[g.id]} <span className="ag-count">· {g.words} words · {g.durationMs ? fmtTime(g.durationMs) : "~" + fmtTime((g.est_s || 0) * 1000) + " est."}{g.heading_text ? " · opens the chapter" : ""}</span></div>
                          <div className="ag-count">
                            {g.status === "queued" ? "Queued" : g.status === "running" ? "Generating…" : g.status === "done" ? "Voiced" : g.status === "error" ? "Failed" : g.status === "truncated" ? "Cut off — splitting…" : "Not generated"}
                            {g.verified ? "" : " · speakers not labelled (narrator reads it all)"}
                            {g.customPrompt ? " · custom prompt" : ""}
                          </div>
                        </div>
                        <div className="ab-actions">
                          <button className="btn-action btn-inline" onClick={function () { togglePrompt(g); }}>{openPrompt[0][g.id] != null ? "Hide prompt" : "Prompt"}</button>
                          <button className="btn-action btn-inline" disabled={rendering} onClick={function () { render([g]); }}>{g.status === "done" ? "Regenerate" : "Generate"}</button>
                        </div>
                      </div>
                      <div className="ag-body">
                        <div className="ab-lines">
                          {(g.lines || []).map(function (l, k) {
                            return (
                              isMetaLine(l) ? (
                                <div key={k} className="ab-line ab-cue">
                                  <span className="ab-cue-tag">Document info</span>
                                  <span>{l.text}</span><span className="ab-delivery">{s.options.metadata === "read" ? "read aloud" : "kept, not spoken"}</span>
                                </div>
                              ) :
                              l.kind === "cue" ? (
                                <div key={k} className="ab-line ab-cue">
                                  <span className="ab-cue-tag">{{ sfx: "SFX", ambience: "Ambience", bgm: "Music" }[l.cue] || "Cue"}</span>
                                  <span>{l.text}</span>{s.options.cues === "read" ? <span className="ab-delivery">read aloud</span> : null}
                                </div>
                              ) :
                              <div key={k} className="ab-line">
                                <span className="ab-speaker" style={{ color: speakerColours[l.speaker] || "var(--text-secondary)" }}>{l.speaker}</span>
                                {l.delivery ? <span className="ab-delivery">{l.delivery}</span> : null}
                                <span className="ab-text">{l.text}</span>
                              </div>
                            );
                          })}
                        </div>
                        {openPrompt[0][g.id] != null ? (
                          <React.Fragment>
                            <div className="field-label" style={{ marginTop: 10 }}>Seed Audio prompt {g.customPrompt ? "(edited)" : ""}</div>
                            <textarea className="video-input ab-mono" rows="10" value={g.customPrompt || openPrompt[0][g.id]}
                                      onChange={function (e) { setSegment(g.id, { customPrompt: e.target.value }); }} />
                            {g.customPrompt ? <button className="btn-link" onClick={function () { setSegment(g.id, { customPrompt: "" }); }}>Reset to generated prompt</button> : null}
                          </React.Fragment>
                        ) : null}
                        {g.audioUrl ? <audio controls preload="none" src={g.audioUrl} style={{ width: "100%", marginTop: 8 }} /> : null}
                        {g.status === "error" ? <div className="error-box">{g.error}</div> : null}
                      </div>
                    </div>
                  </React.Fragment>
                );
              })}
            </div>
            {segs.length > shown.length ? (
              <div className="btn-row"><button className="btn-action" onClick={function () { pageS[1](pageS[0] + 1); }}>Show {Math.min(PAGE, segs.length - shown.length)} more of {segs.length - shown.length}</button></div>
            ) : null}

            {/* -------------------------------------------------- 5. audiobook */}
            <hr className="divider" />
            <div className="field-label">5 · Audiobook</div>
            <div className="video-hint" style={{ marginTop: 0 }}>Joins every voiced segment in order with natural paragraph and chapter pauses, levels the loudness for listening (≈ −18 LUFS), and exports an MP3 and an M4B audiobook with chapter markers.</div>
            <div className="btn-row">
              <button className="btn-primary" disabled={!doneCount || busyS[0] === "assemble" || rendering} onClick={assemble}>
                {busyS[0] === "assemble" ? "Assembling…" : doneCount < segs.length ? "Assemble audiobook (" + doneCount + "/" + segs.length + " segments voiced)" : "Assemble audiobook"}
              </button>
            </div>
            {s.book ? (
              <div className="ag-card" style={{ marginTop: 12, marginBottom: 0 }}>
                <div className="ag-header">
                  <div>
                    <div className="ag-title">{(s.cast && s.cast.title) || "Audiobook"}</div>
                    <div className="ag-count">{fmtTime(s.book.duration_ms)} · {(s.book.chapters || []).length} chapters</div>
                  </div>
                  <div className="ab-actions">
                    <a className="btn-action btn-inline" href={s.book.mp3_url} download style={{ textDecoration: "none" }}>Download MP3</a>
                    <a className="btn-action btn-inline" href={s.book.m4b_url} download style={{ textDecoration: "none" }}>Download M4B</a>
                  </div>
                </div>
                <div className="ag-body">
                  <audio controls src={s.book.mp3_url} style={{ width: "100%" }} />
                  <div className="ab-chapters">
                    {(s.book.chapters || []).map(function (c, i) {
                      return <div key={i} className="ab-chapter-row"><span>{c.title}</span><span>{fmtTime(c.start)}</span></div>;
                    })}
                  </div>
                </div>
              </div>
            ) : null}
          </React.Fragment>
        ) : null}

        {errS[0] ? <div className="error-box">{errS[0]}</div> : null}
      </div>
    );
  }

  function headerStatus(state) {
    var st = state || {};
    if (st.analysisJob && st.analysisJob.status === "running") return "Analyzing…";
    if (st.renderJob && st.renderJob.status === "running") return "Generating " + (st.renderJob.done || 0) + "/" + (st.renderJob.total || 0);
    if (st.book) return "Audiobook ready";
    var segs = st.segments || [];
    if (segs.length) return segs.filter(function (g) { return g.status === "done"; }).length + "/" + segs.length + " voiced";
    return st.fileName ? "Document loaded" : "Ready";
  }

  window.AudiobookTool = AudiobookTool;
  window.audiobookHeaderStatus = headerStatus;
})();
