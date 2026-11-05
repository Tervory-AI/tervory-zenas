"use client";

import { useMemo, useState, type DragEvent, type ReactNode } from "react";
import {
  COST_LINES,
  COST_NOTE,
  FIELD_LABEL,
  FIELDS,
  autoMap,
  batchName,
  canStart,
  dataPath,
  describePasses,
  fileIssue,
  industryHint,
  industryLabel,
  issuesFor,
  parseCsv,
  progressLine,
  toCsv,
  uploadableRows,
  type BatchCounts,
  type Field,
  type Mapping,
  type PassRecord,
  type StartRequest,
  type Table,
} from "./csv";

const STEPS = [
  { id: "organization", label: "Organization" },
  { id: "industry", label: "Industry" },
  { id: "file", label: "File" },
  { id: "run", label: "Run" },
] as const;

type Step = (typeof STEPS)[number]["id"];

export type RunProgress = { summary: BatchCounts | null; records: PassRecord[] };

export function Wizard({
  orgName,
  orgSlot,
  engines,
  enginesError,
  onRetryEngines,
  onStart,
  progress,
  initialStep = "organization",
}: {
  orgName: string | null;
  orgSlot: ReactNode;
  engines: string[] | null;
  enginesError: string | null;
  onRetryEngines: () => void;
  onStart: (request: StartRequest) => Promise<{ batch: string }>;
  progress: RunProgress | null;
  initialStep?: Step;
}) {
  const [step, setStep] = useState<Step>(orgName && initialStep !== "organization" ? initialStep : "organization");
  const [engine, setEngine] = useState("");
  const [table, setTable] = useState<Table | null>(null);
  const [mapping, setMapping] = useState<Mapping | null>(null);
  const [fileLabel, setFileLabel] = useState("");
  const [fileError, setFileError] = useState<string | null>(null);
  const [over, setOver] = useState(false);
  const [busy, setBusy] = useState(false);
  const [runError, setRunError] = useState<string | null>(null);
  const [liveBatch, setLiveBatch] = useState<string | null>(null);

  const issues = useMemo(() => (fileError ? [] : issuesFor(table, mapping)), [fileError, table, mapping]);
  const ready = !fileError && table != null && mapping != null && canStart(issues);
  const count = table && mapping && ready ? uploadableRows(table, mapping).filter((row) => row.values.name).length : 0;
  const shown = table ? table.rows.slice(0, 5) : [];
  const passes = describePasses(progress?.records ?? []);
  const stepIndex = STEPS.findIndex((item) => item.id === step);

  async function take(file: File | undefined) {
    setRunError(null);
    if (!file) return;
    const problem = fileIssue(file);
    setFileLabel(file.name);
    if (problem) {
      setFileError(problem);
      setTable(null);
      setMapping(null);
      return;
    }
    const text = await file.text();
    const parsed = parseCsv(text);
    setFileError(null);
    setTable(parsed);
    setMapping(autoMap(parsed.headers));
  }

  function onDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setOver(false);
    void take(event.dataTransfer.files?.[0]);
  }

  function setColumn(field: Field, value: string) {
    setMapping((current) => ({ ...(current ?? autoMap(table?.headers ?? [])), [field]: value === "" ? null : Number(value) }));
  }

  async function start() {
    if (!table || !mapping || !engine || !ready || busy) return;
    setBusy(true);
    setRunError(null);
    try {
      const result = await onStart({
        batch: batchName(fileLabel),
        csv: toCsv(table, mapping),
        engine,
        count,
      });
      setLiveBatch(result.batch);
      setStep("run");
    } catch (err) {
      setRunError(err instanceof Error ? err.message : "The run did not start. Try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <img className="logo" src="/brand/tervory-logo.png" alt="Tervory AI" height={28} />
      <ol className="steps">
        {STEPS.map((item, index) => (
          <li key={item.id} className={item.id === step ? "here" : index < stepIndex ? "done" : undefined}>
            {index + 1}. {item.label}
          </li>
        ))}
      </ol>

      {step === "organization" && (
        <section>
          <h1>Create or join an organization</h1>
          <p className="lede">An organization is your company’s workspace. Its records stay separate from every other organization.</p>
          {orgName ? (
            <div className="panel">
              <p className="org-name">You are in {orgName}.</p>
              <p className="lede">Continue with this organization, or create or join a different one below.</p>
              <div className="actions">
                <button type="button" className="primary" onClick={() => setStep("industry")}>Continue</button>
              </div>
            </div>
          ) : null}
          <div className="panel" style={{ marginTop: orgName ? 12 : 0 }}>{orgSlot}</div>
        </section>
      )}

      {step === "industry" && (
        <section>
          <h1>Choose an industry</h1>
          <p className="lede">We use that industry’s own list and words. The rest of the work is the same for every industry.</p>
          {enginesError && (
            <div className="panel">
              <p role="alert">{enginesError}</p>
              <button type="button" className="secondary" onClick={onRetryEngines}>Try again</button>
            </div>
          )}
          {engines == null && !enginesError && <p className="loading">Loading industries…</p>}
          {engines != null && engines.length === 0 && !enginesError && <p role="alert">No industries are available yet. Try again in a moment.</p>}
          {engines != null && engines.length > 0 && (
            <fieldset className="industries">
              <legend>Which industry are these records?</legend>
              {engines.map((id) => {
                const hint = industryHint(id);
                return (
                  <label key={id} className="choice">
                    <input type="radio" name="industry" value={id} checked={engine === id} onChange={() => setEngine(id)} />
                    <span>
                      {industryLabel(id)}
                      {hint && <small>{hint}</small>}
                    </span>
                  </label>
                );
              })}
            </fieldset>
          )}
          <div className="actions">
            <button type="button" className="secondary" onClick={() => setStep("organization")}>Back</button>
            <button type="button" className="primary" disabled={!engine} onClick={() => setStep("file")}>Continue</button>
          </div>
        </section>
      )}

      {step === "file" && (
        <section>
          <h1>Add a CSV</h1>
          <p className="lede">Drop the file here, or choose it. You will see the first 5 rows and match the columns. Nothing is uploaded until you press Start.</p>
          <div
            className={over ? "drop over" : "drop"}
            onDragOver={(event) => { event.preventDefault(); setOver(true); }}
            onDragLeave={() => setOver(false)}
            onDrop={onDrop}
          >
            <p>Drop a CSV here</p>
            <label>
              Choose a file
              <input
                type="file"
                accept=".csv,text/csv,text/plain,.txt,.tsv"
                onChange={(event) => void take(event.target.files?.[0])}
              />
            </label>
            {fileLabel && <p className="file-name">{fileLabel}</p>}
          </div>

          <ul className="issues">
            {fileError && <li className="error" role="alert">{fileError}</li>}
            {issues.map((issue) => (
              <li key={issue.message} className={issue.level} role={issue.level === "error" ? "alert" : undefined}>{issue.message}</li>
            ))}
          </ul>

          {table && mapping && (
            <>
              <p className="meta-line">
                {table.rows.length > 5 ? `Showing the first 5 of ${table.rows.length} rows.` : `${table.rows.length} rows.`}
              </p>
              <div className="preview-wrap">
                <table>
                  <thead>
                    <tr>
                      {FIELDS.map((field) => <th key={field}>{FIELD_LABEL[field]}</th>)}
                    </tr>
                  </thead>
                  <tbody>
                    {shown.map((cells, index) => (
                      <tr key={index}>
                        {FIELDS.map((field) => {
                          const column = mapping[field];
                          return <td key={field}>{column == null ? "" : (cells[column] ?? "").trim()}</td>;
                        })}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              <div className="maps">
                {FIELDS.map((field) => (
                  <label key={field} className="map-row">
                    <span>{FIELD_LABEL[field]}</span>
                    <select aria-label={FIELD_LABEL[field]} value={mapping[field] == null ? "" : String(mapping[field])} onChange={(event) => setColumn(field, event.target.value)}>
                      <option value="">Not in this file</option>
                      {table.headers.map((header, index) => (
                        header.trim() ? <option key={`${header}-${index}`} value={index}>{header}</option> : null
                      ))}
                    </select>
                  </label>
                ))}
              </div>
            </>
          )}

          <h2>What this run costs</h2>
          <ul className="costs">
            {COST_LINES.map((line) => (
              <li key={line.id}>
                <span>{line.title}</span>
                <span className="cost">{line.cost}</span>
              </li>
            ))}
          </ul>
          <p className="lede">{COST_NOTE}</p>
          {ready && <p>{count} {count === 1 ? "record" : "records"} will be uploaded when you press Start. Nothing is uploaded until then.</p>}
          {runError && <p className="issues" role="alert"><span className="error" style={{ display: "block", padding: "8px 10px", borderRadius: 8 }}>{runError}</span></p>}

          <div className="actions">
            <button type="button" className="secondary" onClick={() => setStep("industry")}>Back</button>
            <button type="button" className="primary" disabled={!ready || busy} onClick={() => void start()}>{busy ? "Starting…" : "Start"}</button>
          </div>
        </section>
      )}

      {step === "run" && liveBatch && (
        <section aria-live="polite">
          <h1>Your run has started</h1>
          <p className="lede">Each step updates on its own. You can open your records while this is still going.</p>
          <p className="meta-line">{progress ? progressLine(progress.summary, progress.records) : "Starting…"}</p>
          <ul className="passes">
            {(progress ? passes : COST_LINES.map((line) => ({ id: line.id, label: line.title, state: "waiting", detail: "Waiting" }))).map((pass) => (
              <li key={pass.id} className={pass.state}>
                <span>{pass.label}</span>
                <span className="detail">{pass.detail}</span>
              </li>
            ))}
          </ul>
          <div className="actions">
            <a className="primary" href={dataPath(liveBatch)}>See your records</a>
          </div>
        </section>
      )}
    </div>
  );
}
