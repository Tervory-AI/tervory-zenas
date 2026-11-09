"use client";
import { useEffect, useRef, useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { CHANNELS, Data, DASH, value } from "./copy";
import { FirstAction } from "./day-zero";
import { Field, Fields, Records } from "./records";
import { confirmTrigger, proposeTrigger } from "./actions";
import { clientError, waitForAction } from "./request";

type Propose = (
  tool: string,
  args: Data,
  title: string,
  entries: readonly (readonly [string, unknown])[],
) => Promise<void>;
const rows = (input: unknown): Data[] => (Array.isArray(input) ? input : []);
const object = (input: unknown): Data =>
  input && typeof input === "object" && !Array.isArray(input)
    ? (input as Data)
    : {};
export function safeLink(input: unknown): string | undefined {
  try {
    const u = new URL(String(input));
    return (u.protocol === "https:" || u.protocol === "http:") &&
      !u.username &&
      !u.password
      ? u.href
      : undefined;
  } catch {
    return undefined;
  }
}
export default function ConsoleView({
  area,
  section,
  id,
  data,
}: {
  area: string;
  section: string;
  id?: string;
  data: Data;
}) {
  const router = useRouter(),
    [pending, start] = useTransition(),
    [error, setError] = useState(""),
    [preview, setPreview] = useState<{
      thread: string;
      card: string;
      title: string;
      entries: readonly (readonly [string, unknown])[];
    } | null>(null);
  const [result, setResult] = useState<Data | null>(null);
  const busy = useRef(false);
  const propose: Propose = async (tool, args, title, entries) => {
    if (busy.current) return;
    busy.current = true;
    setError("");
    start(async () => {
      try {
        const answer = await waitForAction(proposeTrigger(tool, args));
        setPreview({
          thread: answer.thread_id,
          card: answer.card.card_id,
          title,
          entries: answer.preview ?? entries,
        });
      } catch (e) {
        setError(clientError(e));
      } finally { busy.current = false; }
    });
  };
  const confirm = () => {
    if (!preview || busy.current) return;
    busy.current = true;
    setError("");
    start(async () => {
      try {
        const answer = await waitForAction(confirmTrigger(preview.thread, preview.card));
        setResult(answer);
        if (answer.status === "confirmed") {
          const output = object(answer.result);
          if (
            output.id &&
            ["trigger.post.create", "trigger.post.edit"].includes(
              String(answer.tool),
            )
          )
            router.push(`/trigger/marketing/composer/${output.id}`);
          const link = safeLink(output.authorization_url ?? output.url);
          if (link) {
            const target = new URL(link);
            if (
              target.protocol === "https:" &&
              ["accounts.google.com", "www.facebook.com"].includes(
                target.hostname,
              )
            )
              window.location.assign(link);
          }
          setPreview(null);
          router.refresh();
        }
      } catch (e) {
        setError(clientError(e));
      } finally { busy.current = false; }
    });
  };
  const cancel = () => {
    if (!preview || busy.current) return;
    busy.current = true;
    setError("");
    start(async () => {
      try {
        await waitForAction(confirmTrigger(preview.thread, preview.card, "cancel"));
        setPreview(null);
      } catch (error) { setError(clientError(error)); }
      finally { busy.current = false; }
    });
  };
  return (
    <>
      {!id && !preview && <FirstAction area={area} section={section} data={data} />}
      {preview ? (
        <div className="trigger-stack trigger-confirm">
          <h1>{preview.title}</h1>
          <p>Nothing runs until you confirm.</p>
          <Fields entries={preview.entries} />
          <div className="trigger-confirm-footer">
            <button
              disabled={pending}
              onClick={cancel}
            >
              Cancel
            </button>
            <button className="primary" disabled={pending} onClick={confirm}>
              Confirm
            </button>
          </div>
        </div>
      ) : area === "bot" ? (
        section === "settings" ? (
          <Settings data={data} propose={propose} pending={pending} />
        ) : (
          <Inbox data={data} id={id} propose={propose} pending={pending} />
        )
      ) : section === "channels" ? (
        <Channels data={data} propose={propose} pending={pending} />
      ) : section === "reviews" ? (
        <Reviews data={data} id={id} propose={propose} pending={pending} />
      ) : section === "composer" ? (
        <Composer data={data} id={id} propose={propose} pending={pending} />
      ) : (
        <Posts
          data={data}
          id={id}
          calendar={section === "calendar"}
          propose={propose}
          pending={pending}
        />
      )}
      <div className="trigger-error-space">{error && <p role="alert">{error}</p>}</div>
      {!result && !preview && !id && <p className="trigger-first-receipt">— · Confirm an action to create its receipt.</p>}
      {result && (
        <div className="trigger-section">
          <p>
            {value(
              result.error ??
                object(result.result).platform_error ??
                object(result.result).state ??
                result.status,
            )}
          </p>
          {safeLink(object(result.result).platform_url) && (
            <a
              href={safeLink(object(result.result).platform_url)}
              target="_blank"
              rel="noreferrer"
            >
              {value(object(result.result).platform_url)}
            </a>
          )}
          {rows(result.receipts).map((receipt, index) => (
            <Link
              key={index}
              href={`/ledger?entry=${encodeURIComponent(String(receipt.ledger_id ?? receipt.id))}`}
            >
              {value(receipt.ledger_id ?? receipt.id)}
            </Link>
          ))}
          {!!(result.ledger_id ?? object(result.result).ledger_id) && (
            <Link
              href={`/ledger?entry=${encodeURIComponent(String(result.ledger_id ?? object(result.result).ledger_id))}`}
            >
              {value(result.ledger_id ?? object(result.result).ledger_id)}
            </Link>
          )}
        </div>
      )}
    </>
  );
}
function Channels({
  data,
  propose,
  pending,
}: {
  data: Data;
  propose: Propose;
  pending: boolean;
}) {
  return (
    <>
      <h1>Channels</h1>
      <div className="trigger-channel-list">
        <div className="trigger-channel trigger-channel-heading">
          <span>Channel</span>
          <span>Account</span>
          <span>Status</span>
          <span />
        </div>
        {Object.entries(CHANNELS).map(([channel, title]) => {
          const row = object(data[channel]),
            connected = row.status === "connected",
            supported = ["facebook", "gbp"].includes(channel);
          return (
            <div className="trigger-channel" key={channel}>
              <span>{title}</span>
              <span>
                {connected && rows(row.choices).length ? (
                  <select
                    aria-label="Account"
                    value={String(row.page_id ?? row.location ?? "")}
                    disabled={pending}
                    onChange={(event) =>
                      propose(
                        channel === "gbp"
                          ? "trigger.gbp.location.select"
                          : "trigger.meta.page.select",
                        channel === "gbp"
                          ? { location: event.target.value }
                          : { page_id: event.target.value },
                        "Confirm · Connect",
                        [
                          ["Channel", title],
                          ["Account", event.target.selectedOptions[0].text],
                        ],
                      )
                    }
                  >
                    <option value="">{DASH}</option>
                    {rows(row.choices).map((choice) => (
                      <option
                        key={String(
                          choice.page_id ?? choice.id ?? choice.location,
                        )}
                        value={String(
                          choice.page_id ?? choice.id ?? choice.location,
                        )}
                      >
                        {value(choice.page_name ?? choice.name ?? choice.title)}
                      </option>
                    ))}
                  </select>
                ) : (
                  value(row.page_name ?? row.location_title)
                )}
              </span>
              <span>{connected ? "Connected" : "Off"}</span>
              <button
                disabled={pending || !supported}
                onClick={() =>
                  propose(
                    `trigger.${channel === "gbp" ? "gbp" : "meta"}.${connected ? "disconnect" : "connect"}`,
                    {},
                    `Confirm · ${connected ? "Disconnect" : "Connect"}`,
                    [
                      ["Channel", title],
                      ["Account", row.page_name ?? row.location_title],
                    ],
                  )
                }
              >
                {connected ? "Disconnect" : "Connect"}
              </button>
            </div>
          );
        })}
      </div>
    </>
  );
}
function Checks({ report }: { report: unknown }) {
  const r = object(report);
  return (
    <section className="trigger-section">
      <h2>Trigger checks</h2>
      <Fields
        entries={[
          ["Findings", r.findings ?? JSON.stringify(r.issues ?? null)],
          ["Checker status", r.complete],
          ["Rule", r.rule],
          ["Location", r.location],
        ]}
      />
    </section>
  );
}
function Composer({
  data,
  id,
  propose,
  pending,
}: {
  data: Data;
  id?: string;
  propose: Propose;
  pending: boolean;
}) {
  const current = object(
      data.post ?? rows(data.posts).find((p) => p.id === id),
    ),
    [text, setText] = useState(String(current.text ?? "")),
    [channel, setChannel] = useState(String(current.channel ?? "gbp")),
    [date, setDate] = useState(""),
    [time, setTime] = useState(""),
    [zone, setZone] = useState(String(current.time_zone ?? ""));
  useEffect(() => {
    setText(String(current.text ?? ""));
    setChannel(String(current.channel ?? "gbp"));
  }, [current.id, current.revision]);
  const approved = current.state === "approved",
    postId = current.id;
  const approve = () =>
    propose("trigger.post.approve", { post_id: postId }, "Confirm · Approve", [
      ["Post", text],
      ["Channels", CHANNELS[channel as keyof typeof CHANNELS]],
      ["Checker findings", object(current.report).findings],
    ]);
  return (
    <>
      <h1>Composer</h1>
      <section className="trigger-section">
        <h2>Post</h2>
        <div className="trigger-fields">
          <Field label="Brief">
            <textarea
              value={text}
              maxLength={20000}
              onChange={(e) => setText(e.target.value)}
            />
          </Field>
          <Field label="Saved voice">{value(data.voice)}</Field>
          <Field label="Draft text">{value(current.text)}</Field>
          <Field label="Media">{DASH}</Field>
          <Field label="Channels">
            <select
              value={channel}
              onChange={(e) => setChannel(e.target.value)}
            >
              {Object.entries(CHANNELS).map(([key, label]) => (
                <option
                  disabled={key === "tiktok" || key === "youtube"}
                  value={key}
                  key={key}
                >
                  {label}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Post title">{value(current.title)}</Field>
        </div>
      </section>
      <Checks report={current.report} />
      <div className="trigger-actions">
        <button
          className="primary"
          disabled={pending || !text.trim()}
          onClick={() =>
            postId && text === current.text
              ? approve()
              : propose(
                  postId ? "trigger.post.edit" : "trigger.post.create",
                  postId ? { post_id: postId, text } : { channel, text },
                  "Confirm · Approve",
                  [
                    ["Post", text],
                    ["Channels", CHANNELS[channel as keyof typeof CHANNELS]],
                  ],
                )
          }
        >
          Approve
        </button>
      </div>
      <section className="trigger-section">
        <h2>Schedule</h2>
        <div className="trigger-fields">
          <Field label="Date">
            <input
              type="date"
              value={date}
              onChange={(e) => setDate(e.target.value)}
            />
          </Field>
          <Field label="Time zone">
            <input value={zone} onChange={(e) => setZone(e.target.value)} />
          </Field>
          <Field label="Time">
            <input
              type="time"
              value={time}
              onChange={(e) => setTime(e.target.value)}
            />
          </Field>
        </div>
        <button
          className="primary"
          disabled={pending || !approved || !date || !time || !zone}
          onClick={() =>
            propose(
              "trigger.post.schedule",
              {
                post_id: postId,
                local_date: date,
                local_time: time,
                time_zone: zone,
              },
              "Confirm · Schedule",
              [
                ["Post", current.text],
                ["Date", date],
                ["Time zone", zone],
                ["Time", time],
              ],
            )
          }
        >
          Schedule
        </button>
        {!approved && <p>Approve the post before scheduling.</p>}
      </section>
      {Object.entries(CHANNELS).map(([key, label]) => {
        const length =
          key === current.channel ? object(object(current.report).length) : {};
        return (
          <section className="trigger-section" key={key}>
            <h2>{label} preview</h2>
            <Fields
              entries={[
                ["Preview", key === channel ? text : null],
                ["Length limit", length.limit],
                ["Counted as", length.unit],
                ["Length used", length.count],
              ]}
            />
          </section>
        );
      })}
    </>
  );
}
function Posts({
  data,
  id,
  calendar,
  propose,
  pending,
}: {
  data: Data;
  id?: string;
  calendar: boolean;
  propose: Propose;
  pending: boolean;
}) {
  const posts = rows(data.posts),
    post = object(data.post ?? posts.find((p) => p.id === id));
  if (id)
    return (
      <>
        <Link href="/trigger/marketing/calendar">← Calendar</Link>
        <h1>Post detail</h1>
        <Fields
          entries={[
            ["Channel", CHANNELS[post.channel as keyof typeof CHANNELS]],
            [
              "Status",
              [post.state, post.platform_error].filter(Boolean).join("\n"),
            ],
            ["Text", post.text],
            ["Media", null],
            [
              "Status history",
              rows(post.events).map((e) => `${e.created_at}: ${e.state}`),
            ],
            ["Reported at", post.published_at],
            ["Reach", null],
            ["Clicks", null],
            ["Comments", null],
            ["Post title", post.title],
          ]}
        />
        {safeLink(post.platform_url) && (
          <a
            href={safeLink(post.platform_url)}
            rel="noreferrer"
            target="_blank"
          >
            {value(post.platform_url)}
          </a>
        )}
        {!!post.ledger_id && (
          <Link
            href={`/ledger?entry=${encodeURIComponent(String(post.ledger_id))}`}
          >
            {value(post.ledger_id)}
          </Link>
        )}
        <div className="trigger-actions">
          <Link href={`/trigger/marketing/composer/${id}`}>Composer</Link>
          <button
            disabled={
              pending || ["published", "cancelled"].includes(String(post.state))
            }
            onClick={() =>
              propose(
                "trigger.post.cancel",
                { post_id: id },
                "Confirm · Cancel",
                [
                  ["Post", post.text],
                  ["Status", post.state],
                ],
              )
            }
          >
            Cancel
          </button>
        </div>
      </>
    );
  return calendar ? (
    <Calendar posts={posts} />
  ) : (
    <>
      <h1>Google posts</h1>
      <Records
        columns={[
          ["text", "Post"],
          ["channel", "Channel"],
          ["state", "Status"],
          ["planned_at", "Date"],
          ["time_zone", "Time zone"],
        ]}
        items={posts.filter((p) => p.channel === "gbp")}
        href={(row) => `/trigger/marketing/posts/${row.id}`}
      />
    </>
  );
}
function Reviews({
  data,
  id,
  propose,
  pending,
}: {
  data: Data;
  id?: string;
  propose: Propose;
  pending: boolean;
}) {
  const reviews = rows(data.reviews),
    review = reviews.find((r) => r.id === id),
    reply = rows(data.replies).find(
      (r) =>
        r.review_id === review?.review_id && r.location === review?.location,
    ),
    [text, setText] = useState(String(reply?.text ?? ""));
  useEffect(() => {
    setText(String(reply?.text ?? ""));
  }, [reply?.id, reply?.revision]);
  if (!id)
    return (
      <>
        <h1>Google reviews</h1>
        {!reviews.length ? (
          <p>No reviews yet.</p>
        ) : (
          <Records
            columns={[
              ["text", "Review"],
              ["rating", "Rating"],
              ["updated_at", "Received at"],
            ]}
            items={reviews}
            href={(row) => `/trigger/marketing/reviews/${row.id}`}
          />
        )}
      </>
    );
  if (!review) return null;
  return (
    <>
      <Link href="/trigger/marketing/reviews">← Google reviews</Link>
      <h1>Review detail</h1>
      <div className="trigger-fields">
        <Field label="Profile">{value(review.location)}</Field>
        <Field label="Review">{value(review.text)}</Field>
        <Field label="Rating">{value(review.rating)}</Field>
        <Field label="Received at">{value(review.updated_at)}</Field>
        <Field label="Reply brief">{value(review.text)}</Field>
        <Field label="Draft reply">
          <textarea
            maxLength={20000}
            value={text}
            onChange={(e) => setText(e.target.value)}
          />
        </Field>
        <Field label="Saved voice">{value(data.voice)}</Field>
        <Field label="Reply status">{value(reply?.state)}</Field>
      </div>
      <Checks report={reply?.report} />
      <button
        className="primary"
        disabled={pending}
        onClick={() =>
          propose(
            !reply
              ? "trigger.gbp.reply.draft"
              : text !== reply.text
                ? "trigger.gbp.reply.edit"
                : "trigger.gbp.reply.send",
            !reply
              ? { review_id: review.review_id }
              : text !== reply.text
                ? { reply_id: reply.id, text }
                : { reply_id: reply.id },
            "Confirm · Send reply",
            [
              ["Profile", review.location],
              ["Review", review.text],
              ["Reply", text],
              ["Checker findings", object(reply?.report).findings],
            ],
          )
        }
      >
        Send reply
      </button>
    </>
  );
}
const SETTINGS = [
  ["opening_hours", "Opening hours"],
  ["time_zone", "Time zone"],
  ["greeting", "Greeting"],
  ["catalogue", "Catalogue"],
  ["faqs", "FAQs"],
  ["documents", "Documents"],
  ["handover_rules", "Hand-over rules"],
  ["no_source_message", "No-source message"],
  ["handover_message", "Hand-over message"],
] as const;
function Settings({
  data,
  propose,
  pending,
}: {
  data: Data;
  propose: Propose;
  pending: boolean;
}) {
  const [settings, setSettings] = useState(object(data.settings)),
    sources = rows(data.sources);
  return (
    <>
      <h1>Settings</h1>
      <div className="trigger-actions">
        <button
          role="switch"
          aria-checked={!!settings.enabled}
          onClick={() =>
            setSettings({ ...settings, enabled: !settings.enabled })
          }
        >
          <img
            src={`/trigger/toggle-${settings.enabled ? "on" : "off"}.svg`}
            width={48}
            height={32}
            alt=""
          />
          {settings.enabled ? "On" : "Off"}
        </button>
        <button
          className="primary"
          disabled={pending || !settings.time_zone || !settings.name}
          onClick={() =>
            propose("trigger.bot.settings", settings, "Confirm · Settings", [
              ["Bot", settings.enabled ? "On" : "Off"],
              ...SETTINGS.map(
                ([key, label]) => [label, settings[key]] as const,
              ),
            ])
          }
        >
          Save settings
        </button>
      </div>
      <div className="trigger-fields">
        {SETTINGS.map(([key, label]) => (
          <Field key={key} label={label}>
            {["catalogue", "faqs", "documents"].includes(key) ? (
              value(
                sources
                  .filter(
                    (s) =>
                      s.kind ===
                      (
                        {
                          catalogue: "rig",
                          faqs: "faq",
                          documents: "document",
                        } as Record<string, string>
                      )[key],
                  )
                  .map((s) => s.title),
              )
            ) : (
              <textarea
                maxLength={2000}
                value={String(settings[key] ?? "")}
                onChange={(e) =>
                  setSettings({ ...settings, [key]: e.target.value })
                }
              />
            )}
          </Field>
        ))}
      </div>
    </>
  );
}
function Inbox({
  data,
  id,
  propose,
  pending,
}: {
  data: Data;
  id?: string;
  propose: Propose;
  pending: boolean;
}) {
  const [text, setText] = useState(""),
    [person, setPerson] = useState(""),
    conversation = object(data.conversation),
    messages = rows(data.messages);
  if (!id)
    return (
      <>
        <h1>Inbox</h1>
        <Records
          columns={[
            ["visitor_id", "Visitor"],
            ["last_message", "Last message"],
            ["status", "Status"],
            ["person", "Assigned person"],
            ["updated_at", "Updated at"],
          ]}
          items={rows(data.conversations)}
          href={(row) => `/trigger/bot/inbox/${row.id}`}
        />
      </>
    );
  return (
    <>
      <Link href="/trigger/bot/inbox">← Inbox</Link>
      <h1>Conversation detail</h1>
      <div className="trigger-actions">
        <button
          disabled={pending}
          onClick={() =>
            propose(
              "trigger.bot.handover",
              { conversation_id: id, text },
              "Confirm · Hand-over",
              [
                ["Conversation", id],
                ["Person", person],
                ["Reason", text],
              ],
            )
          }
        >
          Hand-over
        </button>
        <button
          className="primary"
          disabled={pending || !text.trim() || !person.trim()}
          onClick={() =>
            propose(
              "trigger.bot.reply",
              { conversation_id: id, text, person },
              "Confirm · Send reply",
              [
                ["Conversation", id],
                ["Recipient", conversation.visitor_id],
                ["Reply", text],
              ],
            )
          }
        >
          Send reply
        </button>
      </div>
      <div className="trigger-fields">
        <Field label="Visitor">{value(conversation.visitor_id)}</Field>
        <Field label="Status">{value(conversation.status)}</Field>
        <Field label="Assigned person">
          <input
            value={person}
            maxLength={200}
            onChange={(e) => setPerson(e.target.value)}
          />
        </Field>
        <Field label="Sources used">
          {value(messages.flatMap((m) => rows(m.cited)))}
        </Field>
        <Field label="Reply">
          <textarea
            value={text}
            maxLength={2000}
            onChange={(e) => setText(e.target.value)}
          />
        </Field>
      </div>
      <section className="trigger-section">
        <h2>Transcript</h2>
        {messages.map((message) => (
          <div key={String(message.id)} className="trigger-stack">
            {!!message.question && (
              <>
                <p>Visitor</p>
                <Fields entries={[["Message", message.question]]} />
              </>
            )}
            {!!message.answer && (
              <>
                <p>{message.person ? "Person" : "Bot"}</p>
                <Fields
                  entries={
                    message.person
                      ? [["Message", message.answer]]
                      : [
                          ["Message", message.answer],
                          ["Who answered", "Bot"],
                          ["Sources", message.cited],
                        ]
                  }
                />
              </>
            )}
          </div>
        ))}
      </section>
      {rows(data.drafts).map((draft) => (
        <section className="trigger-section" key={String(draft.id)}>
          <h2>{draft.kind === "lead" ? "Lead draft" : "Quote draft"}</h2>
          <Fields entries={draftFields(draft, id)} />
          <button
            className="primary"
            disabled={pending || draft.state !== "draft"}
            onClick={() =>
              propose(
                `trigger.bot.${draft.kind}.confirm`,
                { draft_id: draft.id },
                draft.kind === "lead" ? "Confirm · Lead" : "Confirm · Quote",
                [
                  ["Draft status", draft.state],
                  ["Source conversation", id],
                  ...Object.entries(object(draft.data)),
                ],
              )
            }
          >
            Confirm
          </button>
        </section>
      ))}
    </>
  );
}

function Calendar({ posts }: { posts: Data[] }) {
  const [period, setPeriod] = useState("month"),
    [date, setDate] = useState(new Date().toISOString().slice(0, 10)),
    [zone, setZone] = useState(
      String(posts.find((p) => p.time_zone)?.time_zone ?? ""),
    );
  const anchor = new Date(date + "T00:00:00Z"),
    start = new Date(anchor);
  if (period === "month") start.setUTCDate(1);
  start.setUTCDate(start.getUTCDate() - ((start.getUTCDay() + 6) % 7));
  const count =
    period === "week"
      ? 7
      : Math.ceil(
          (new Date(
            Date.UTC(anchor.getUTCFullYear(), anchor.getUTCMonth() + 1, 0),
          ).getUTCDate() +
            ((new Date(
              Date.UTC(anchor.getUTCFullYear(), anchor.getUTCMonth(), 1),
            ).getUTCDay() +
              6) %
              7)) /
            7,
        ) * 7;
  const local = (post: Data) => {
    if (!post.planned_at || !zone) return null;
    try {
      const parts = new Intl.DateTimeFormat("en-CA", {
          timeZone: zone,
          year: "numeric",
          month: "2-digit",
          day: "2-digit",
          hour: "2-digit",
          minute: "2-digit",
          hourCycle: "h23",
        }).formatToParts(new Date(String(post.planned_at))),
        get = (key: string) => parts.find((p) => p.type === key)?.value;
      return {
        date: `${get("year")}-${get("month")}-${get("day")}`,
        time: `${get("hour")}:${get("minute")}`,
      };
    } catch {
      return null;
    }
  };
  return (
    <>
      <h1>Calendar · {period === "month" ? "Month" : "Week"}</h1>
      <div className="trigger-actions">
        <button
          onClick={() => setPeriod("month")}
          aria-pressed={period === "month"}
        >
          Month
        </button>
        <button
          onClick={() => setPeriod("week")}
          aria-pressed={period === "week"}
        >
          Week
        </button>
        <Link className="trigger-button" href="/trigger/marketing/composer">
          New post
        </Link>
      </div>
      <div className="trigger-fields">
        <Field label="Period">
          <input
            type="date"
            value={date}
            onChange={(e) => {
              if (e.target.value) setDate(e.target.value);
            }}
          />
        </Field>
        <Field label="Time zone">
          <input value={zone} onChange={(e) => setZone(e.target.value)} />
        </Field>
      </div>
      <div className="trigger-calendar">
        {["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map((day) => (
          <span key={day}>{day}</span>
        ))}
        {Array.from({ length: count }, (_, index) => {
          const day = new Date(start);
          day.setUTCDate(day.getUTCDate() + index);
          const iso = day.toISOString().slice(0, 10);
          return (
            <div key={iso} className="trigger-calendar-cell">
              <span>{day.getUTCDate()}</span>
              {posts
                .filter((post) => local(post)?.date === iso)
                .map((post) => (
                  <Link
                    key={String(post.id)}
                    className="trigger-post-card"
                    href={`/trigger/marketing/calendar/${post.id}`}
                  >
                    <span>
                      {value(CHANNELS[post.channel as keyof typeof CHANNELS])}
                    </span>
                    <span>{value(post.state)}</span>
                    <span>{local(post)?.time}</span>
                  </Link>
                ))}
            </div>
          );
        })}
      </div>
      <p>Draft · Awaiting approval · Scheduled · Published · Failed</p>
    </>
  );
}

function draftFields(
  draft: Data,
  conversation: string,
): readonly (readonly [string, unknown])[] {
  const data = object(draft.data);
  return draft.kind === "lead"
    ? [
        ["Draft status", draft.state],
        ["First name", data.first_name],
        ["Last name", data.last_name],
        ["Email", data.email],
        ["Phone", data.phone],
        ["Source conversation", data.source_ref ?? conversation],
        ["Notes", data.description],
      ]
    : [
        ["Draft status", draft.state],
        ["Title", data.title],
        ["Contact", data.contact],
        ["Request", data.request],
        [
          "Line items",
          data.line_items ??
            [data.product_id, data.quantity]
              .filter((v) => v !== undefined)
              .join(" · "),
        ],
        ["Source conversation", conversation],
      ];
}
