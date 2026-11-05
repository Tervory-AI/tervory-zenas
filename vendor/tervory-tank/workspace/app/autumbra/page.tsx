import "./public.css";

export const dynamic = "force-dynamic";

/** Public Autumbra product page — no SigninDirectory. Founder console stays at /admin/autumbra. */
export default function AutumbraPublicPage() {
  return (
    <main className="autumbra-public">
      <header className="autumbra-public-head">
        <img src="/brand/tervory-logo.png" width="160" height="36" alt="Tervory" />
        <p className="autumbra-public-eyebrow">Autumbra</p>
        <h1>Watch the edge.<br />Keep the proof.</h1>
        <p className="autumbra-public-lede">
          Autumbra Watch finds availability, DNS, certificate, and header problems across your Tervory
          products. Autumbra Enforce proposes recoveries for a founder to approve — never silent auto-fix
          from this page.
        </p>
      </header>


      <section className="autumbra-public-roles">
        <h2>Watch and Enforce</h2>
        <div>
          <article>
            <h3>Autumbra Watch</h3>
          </article>
          <article>
            <h3>Autumbra Enforce</h3>
            <p>Founder-gated proposals only. No keys, merges, or production changes from an unattended path.</p>
          </article>
        </div>
      </section>

      <footer className="autumbra-public-foot">
      </footer>
    </main>
  );
}
