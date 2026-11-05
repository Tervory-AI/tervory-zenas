import { startCheckout } from "./actions";
import { COPY, type BillingView, type InvoiceView, type PlanView, type UsageView } from "./billing";
import { ChooseButton } from "./choose";

const TABS = [
  { id: "plans", href: "/billing", label: "Plans" },
  { id: "usage", href: "/billing?tab=usage", label: "Usage" },
  { id: "invoices", href: "/billing?tab=invoices", label: "Invoices" },
] as const;

export type BillingTab = (typeof TABS)[number]["id"];

export function billingTab(value: string | undefined): BillingTab {
  if (value === "usage" || value === "invoices") return value;
  return "plans";
}

export function BillingScreen({
  tab,
  view,
  notice,
  addon,
}: {
  tab: BillingTab;
  view: BillingView;
  notice: string | null;
  addon?: React.ReactNode;
}) {
  const title = tab === "usage" ? COPY.usageTitle : tab === "invoices" ? COPY.invoicesTitle : "Billing";
  const lead = tab === "usage" ? COPY.usageLead : tab === "invoices" ? COPY.invoicesLead : COPY.lead;
  return (
    <div className="billing">
      <p className="kicker">Billing</p>
      <h1>{title}</h1>
      <p className="lede">{lead}</p>
      {notice ? <p className="notice">{notice}</p> : null}
      {view.error ? <p className="problem">{view.error}</p> : null}
      <nav className="tabs" aria-label="Billing sections">
        {TABS.map((item) => (
          <a key={item.id} href={item.href} className={item.id === tab ? "here" : undefined} aria-current={item.id === tab ? "page" : undefined}>
            {item.label}
          </a>
        ))}
      </nav>
      {tab === "plans" ? <Plans view={view} /> : null}
      {tab === "plans" ? addon : null}
      {tab === "usage" ? <Usage usage={view.usage} /> : null}
      {tab === "invoices" ? <Invoices invoices={view.invoices} /> : null}
    </div>
  );
}

function Plans({ view }: { view: BillingView }) {
  if (view.finalising) {
    return (
      <section className="panel">
        <h2>{COPY.finalisingTitle}</h2>
        <p>{COPY.finalisingBody}</p>
        <p className="caption">{COPY.noPlan}</p>
      </section>
    );
  }
  if (view.plansNote) {
    return (
      <section className="panel">
        <h2>{view.plansNote}</h2>
        <p className="caption">{COPY.noPlan}</p>
      </section>
    );
  }
  if (view.plans.length === 0) {
    return (
      <section className="panel">
        <h2>{COPY.noPlan}</h2>
      </section>
    );
  }
  return (
    <>
      <div className="plan-row">
        {view.plans.map((plan) => (
          <PlanCard key={plan.priceId} plan={plan} />
        ))}
      </div>
      <p className="caption">{COPY.checkoutFoot}</p>
    </>
  );
}

function PlanCard({ plan }: { plan: PlanView }) {
  return (
    <section className="panel plan">
      {plan.current ? <span className="chip">{COPY.current}</span> : null}
      <h2>{plan.name}</h2>
      <p className="amount">{plan.amount}</p>
      <p className="caption">{plan.caption}</p>
      {plan.description ? <p className="caption">{plan.description}</p> : null}
      {plan.current ? null : (
        <form action={startCheckout}>
          <input type="hidden" name="priceId" value={plan.priceId} />
          <ChooseButton />
        </form>
      )}
    </section>
  );
}

function Usage({ usage }: { usage: UsageView }) {
  return (
    <>
      <div className="tiles">
        <div className="tile">
          <b className="amount">{usage.period}</b>
          <span>{COPY.periodLabel}</span>
        </div>
        <div className="tile">
          <b className="plan-name">{usage.planName}</b>
          <span>{COPY.planLabel}</span>
        </div>
        <div className="tile">
          <b className="amount">{usage.amountDue}</b>
          <span>{COPY.dueLabel}</span>
        </div>
      </div>
      <section className="panel">
        <h2>{COPY.periodHeading}</h2>
        <div className="sheet lines">
          <div className="head">
            <span>Item</span>
            <span>Quantity</span>
            <span>Amount</span>
          </div>
          {usage.lines.length === 0 ? (
            <div className="line">
              <span>{COPY.usageEmpty}</span>
              <span className="amount">{COPY.dash}</span>
              <span className="amount">{COPY.dash}</span>
            </div>
          ) : (
            usage.lines.map((line, index) => (
              <div className="line" key={`${line.item}-${index}`}>
                <span>{line.item}</span>
                <span className="amount">{line.quantity}</span>
                <span className="amount">{line.amount}</span>
              </div>
            ))
          )}
        </div>
        <p className="caption">{COPY.usageCaption}</p>
      </section>
    </>
  );
}

function Invoices({ invoices }: { invoices: InvoiceView[] }) {
  if (invoices.length === 0) {
    return (
      <section className="panel">
        <h2>{COPY.invoicesEmpty}</h2>
        <p className="caption">{COPY.invoicesEmptyBody}</p>
      </section>
    );
  }
  return (
    <section className="panel">
      <div className="sheet invoices">
        <div className="head">
          <span>Date</span>
          <span>Number</span>
          <span>Status</span>
          <span>Amount</span>
          <span />
        </div>
        {invoices.map((invoice) => (
          <div className="line" key={`${invoice.number}-${invoice.date}-${invoice.amount}`}>
            <span className="amount">{invoice.date}</span>
            <span>{invoice.number}</span>
            <span className={invoice.paid ? "paid" : "status"}>{invoice.status}</span>
            <span className="amount">{invoice.amount}</span>
            {invoice.href ? (
              <a href={invoice.href} rel="noopener noreferrer">
                {COPY.openInvoice}
              </a>
            ) : (
              <span className="caption">{COPY.receiptPending}</span>
            )}
          </div>
        ))}
      </div>
      <p className="caption">{COPY.invoicesCaption}</p>
    </section>
  );
}
