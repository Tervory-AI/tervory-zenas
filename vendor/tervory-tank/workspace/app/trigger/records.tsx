import Link from "next/link";
import { cloneElement, isValidElement, useId, type ReactElement } from "react";
import { FirstAction } from "./day-zero";
import { CRM, Data, Resource, value } from "./copy";
export function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  const fieldId = useId();
  const control = isValidElement(children) && ["input", "textarea", "select"].includes(String(children.type));
  const labelled = control ? cloneElement(children as ReactElement<{"aria-labelledby"?: string}>, {
    "aria-labelledby": (children.props as {"aria-labelledby"?: string})["aria-labelledby"] ?? fieldId,
  }) : children;
  return (
    <div className="trigger-field">
      <span id={fieldId} className="trigger-field-label">{label}</span>
      <div className="trigger-field-value">{labelled}</div>
    </div>
  );
}
export function Fields({
  entries,
}: {
  entries: readonly (readonly [string, unknown])[];
}) {
  return (
    <div className="trigger-fields">
      {entries.map(([label, item]) => (
        <Field label={label} key={label}>
          {value(item)}
        </Field>
      ))}
    </div>
  );
}
export function Records({
  columns,
  items,
  href,
  mobileColumns,
}: {
  columns: readonly (readonly [string, string])[];
  items: Data[];
  href: (row: Data) => string;
  mobileColumns?: readonly string[];
}) {
  return (
    <>
      <div className="trigger-table-scroll">
        <table className="trigger-table">
          <thead>
            <tr>
              {columns.map(([key, label]) => (
                <th key={key} scope="col">
                  {label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {items.map((row) => (
              <tr key={String(row.id)} data-record>
                {columns.map(([key, label]) => (
                  <td key={key}>
                    <Link
                      href={href(row)}
                      aria-label={`${label}: ${value(row[key])}`}
                    >
                      {value(row[key])}
                    </Link>
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="trigger-mobile-records">
        {items.map((row) => (
          <Link
            className="trigger-record"
            key={String(row.id)}
            href={href(row)}
          >
            <span>
              {(mobileColumns ?? columns.map(([key]) => key))
                .slice(0, 2)
                .map((key) => (
                  <b key={key}>{value(row[key])}</b>
                ))}
            </span>
            <span>
              {(mobileColumns ?? columns.map(([key]) => key))
                .slice(-2)
                .map((key) => (
                  <b key={key}>{value(row[key])}</b>
                ))}
            </span>
          </Link>
        ))}
      </div>
    </>
  );
}
export default function CRMView({
  resource,
  data,
  detail,
}: {
  resource: Resource;
  data: Data;
  detail: boolean;
}) {
  const spec = CRM[resource],
    base = `/trigger/crm/${resource}`;
  const columns = spec.columns.map(
    (key) =>
      [key, spec.fields.find((field) => field[0] === key)?.[1] ?? key] as const,
  );
  const row = (data.item ?? {}) as Data;
  const title =
    resource === "leads" || resource === "contacts"
      ? [row.first_name, row.last_name].filter(Boolean).join(" ")
      : value(
          row.invoice_number ??
            row.estimate_number ??
            row.invoice_title ??
            row.title,
        );
  return detail ? (
    <>
      <Link href={base}>← {spec.title}</Link>
      <h1>{title}</h1>
      <div className="trigger-actions trigger-record-actions">
        {(resource === "invoices"
          ? ["Send", "Mark paid", "Record payment"]
          : resource === "estimates"
            ? ["Send", "Convert", "Delete"]
            : ["Delete"]
        ).map((action) => (
          <button
            key={action}
            className={action === "Delete" ? "destructive" : undefined}
            disabled
          >
            {action}
          </button>
        ))}
      </div>
      <Fields entries={spec.fields.map(([key, label]) => [label, row[key]])} />
      {resource === "invoices" && (
        <>
          <InvoiceRows
            title="Items"
            columns={[
              ["name", "Item"],
              ["quantity", "Quantity"],
              ["unit_price", "Unit price"],
              ["subtotal", "Subtotal"],
            ]}
            items={row.line_items}
          />
          <InvoiceRows
            title="Payments"
            columns={[
              ["amount", "Amount"],
              ["payment_date", "Payment date"],
              ["payment_method", "Payment method"],
              ["reference_number", "Reference number"],
            ]}
            items={row.payments}
          />
        </>
      )}
    </>
  ) : (
    <>
      <h1>{spec.title}</h1>
      <FirstAction area="crm" section={resource} data={data} />
      <Records
        columns={columns}
        items={((data.items ?? []) as Data[]).map((row) => ({
          ...row,
          _fullname: [row.first_name, row.last_name].filter(Boolean).join(" "),
        }))}
        mobileColumns={
          resource === "leads"
            ? ["_fullname", "company_name", "next_follow_up", "status"]
            : resource === "contacts"
              ? ["_fullname", "company_name", "created_at", "do_not_call"]
              : [
                  resource === "estimates"
                    ? "estimate_number"
                    : "invoice_number",
                  "client_name",
                  "total_amount",
                  "status",
                ]
        }
        href={(row) => `${base}/${row.id}`}
      />
    </>
  );
}

function InvoiceRows({
  title,
  columns,
  items,
}: {
  title: string;
  columns: readonly (readonly [string, string])[];
  items: unknown;
}) {
  return (
    <section className="trigger-stack">
      <h2>{title}</h2>
      <div className="trigger-subtable">
        <table className="trigger-table">
          <thead>
            <tr>
              {columns.map(([key, label]) => (
                <th key={key} scope="col">
                  {label}
                </th>
              ))}
              <th />
            </tr>
          </thead>
          <tbody>
            {(Array.isArray(items) ? items : []).map(
              (row: Data, index: number) => (
                <tr key={String(row.id ?? index)}>
                  {columns.map(([key]) => (
                    <td key={key}>
                      {value(
                        row[key] ??
                          (key === "name" ? row.product_name : undefined),
                      )}
                    </td>
                  ))}
                  <td>
                    <button className="destructive" disabled>
                      Delete
                    </button>
                  </td>
                </tr>
              ),
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}
