import { ProductPreview } from "./preview";

export type ProductJob = {
  id: string; product_id: string; status: string; reason: string | null;
  report: { dimensions_mm: number[]; anchors: Record<string, { position_mm: number[]; quaternion: number[] }> } | null;
};
type Props = {
  jobs: ProductJob[]; enabled: boolean; fewPhotos?: boolean; error?: string; notice?: string;
  jobId: string; action: (form: FormData) => Promise<void>;
};
const LABELS: Record<string, string> = { queued: "Waiting", running: "Preparing", draft: "Ready to review", approved: "Approved", failed: "Could not prepare" };
const NOTICES: Record<string, string> = {
  submit: "The product is being prepared. Refresh to check its progress.",
  approve: "The product view was approved.", mounts: "The attachment points were saved.",
  reset: "Reset requested. The connected terminal will clear on its next check.",
  failed: "The request could not be completed. Check the details and try again. No success was confirmed.",
};

function TextField({ label, name }: { label: string; name: string }) {
  return <label>{label}<input name={name} required maxLength={64} pattern="[A-Za-z0-9][A-Za-z0-9_.-]*" /></label>;
}

export function ThreeDAdminScreen({ jobs, enabled, fewPhotos = false, error, notice, jobId, action }: Props) {
  const drafts = jobs.filter(job => job.status === "draft");
  return <div className="three-admin">
    <p className="kicker">Rig · Administration</p>
    <h1>3D product views</h1>
    <p>Check each product before shoppers see it. Only approved products and parts that fit can appear together.</p>
    <p><a href="/billing">View billing</a> · <a href="/rig/admin/three-d/">Refresh progress</a></p>
    {notice && NOTICES[notice] ? <p role="status" className="three-notice">{NOTICES[notice]}</p> : null}
    {error ? <p role="alert" className="three-notice">{error}</p> : null}
    {!enabled ? <section><h2>3D is not available for this organisation</h2><p>Check your add-on in billing.</p></section> : <>
      <section>
        <h2>Prepare a product</h2>
        <p>Use photos already uploaded to your shop. Enter one file name per line, including its extension. Each photo must be a JPEG or PNG, at most 20 MB. A job can contain up to 70 photos and 1 GB in total.</p>
        <form action={action}>
          <input type="hidden" name="operation" value="submit" /><input type="hidden" name="jobId" value={jobId} />
          <TextField label="Product code" name="product" />
          <label>Starting files<select name="source" defaultValue="scan">
            {fewPhotos ? <option value="ai">One to four photos (.jpg or .png)</option> : null}
            <option value="scan">50 to 70 overlapping photos (.jpg or .png)</option>
          </select></label>
          <label>Uploaded file names<textarea name="files" required rows={3} maxLength={21000} /></label>
          <div className="three-fields">{[["Width", "width"], ["Height", "height"], ["Depth", "depth"]].map(([label, name]) =>
            <label key={name}>{label} (mm)<input name={name} type="number" min="0.01" max="100000" step="0.01" required /></label>)}</div>
          <label>Measured attachment points<textarea name="anchors" required rows={3} maxLength={8000} aria-describedby="three-points-help" /></label>
          <p id="three-points-help">One point per line: name, X, Y, Z in millimetres, then four rotation values (X, Y, Z, W). Use 0, 0, 0, 1 for no rotation. Position is measured from the product file’s origin.</p>
          <label>Maximum preparation cost (USD)<input name="limit" type="number" min="0" max="100" step="0.01" defaultValue="0" required /></label>
          <button type="submit">Prepare product view</button>
        </form>
      </section>
      <section><h2>Products to review</h2>
        {!drafts.length ? <p>No products are waiting for approval.</p> : drafts.map(job => <article key={job.id}>
          <h3>{job.product_id}</h3>
          <p>Dimensions: {job.report?.dimensions_mm.join(" × ")} mm</p>
          <p>Attachment points: {Object.keys(job.report?.anchors ?? {}).join(", ")}</p>
          <ProductPreview id={job.id} anchors={job.report?.anchors ?? {}} />
          <form action={action}>
            <input type="hidden" name="operation" value="approve" /><input type="hidden" name="id" value={job.id} />
            <label className="three-check"><input type="checkbox" name="scale" required />I checked the real dimensions.</label>
            <label className="three-check"><input type="checkbox" name="appearance" required />I checked the appearance from every side.</label>
            <label className="three-check"><input type="checkbox" name="attachment_points" required />I checked the attachment points.</label>
            <button type="submit">Approve product view</button>
          </form>
        </article>)}
      </section>
      <section><h2>Preparation progress</h2>{!jobs.length ? <p>No products have been submitted.</p> : <ul>{jobs.map(job =>
        <li key={job.id}><strong>{job.product_id}</strong> — {LABELS[job.status] ?? "Check progress"}
          {job.status === "failed" ? <p>The product did not pass preparation. Check the files and dimensions before trying again.</p> : null}
        </li>)}</ul>}</section>
      <section><h2>Attach an approved part</h2><p>The part must already have an approved fit to this product or its family. Use the named points from the approved product views.</p>
        <form action={action}><input type="hidden" name="operation" value="mounts" />
          <TextField label="Centre product code" name="centre" /><TextField label="Part code" name="part" />
          <TextField label="Point on the centre product" name="centre_anchor" /><TextField label="Point on the part" name="part_anchor" />
          <button type="submit">Save attachment points</button>
        </form>
      </section>
      <section><h2>Reset a showroom terminal</h2><p>This clears the current selection and contact details when the terminal is connected.</p>
        <form action={action}><input type="hidden" name="operation" value="reset" />
          <label>Terminal ID<input name="id" required pattern="[0-9a-fA-F-]{36}" maxLength={36} /></label>
          <button type="submit">Reset terminal</button>
        </form>
      </section>
    </>}
  </div>;
}
