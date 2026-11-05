import type { DashboardRead } from "@/lib/tank";
import type { Paying, Signups } from "./sources";
const greenDot = "/dashboards/control/status-dot-green.svg";
const amberDot = "/dashboards/control/status-dot-amber.svg";
const greyDot = "/dashboards/control/status-dot-grey.svg";
import { ControlActions, NeedsYou, WorkersActions } from "./widgets";
import { checkingIn, type WorkersRead } from "./workers";
import { count, money, passedSkills, signupAge, sourceLink, sparkPoints, wholeNumber, type activity, type Candidate, type DailyValue } from "./values";

export type FounderView = {
  data: DashboardRead; signups: Signups; paying: Paying;
  activity: ReturnType<typeof activity>; paused: boolean; workersPaused: boolean;
  models: Candidate[]; checks: number; now: number; workers?: WorkersRead | null;
};

const squads = [
  ["base", "Base"], ["tiger", "Tiger"], ["market_lab", "Market Lab"], ["rig", "Rig"],
  ["tisan", "Tisan"], ["radix", "Radix"], ["zenas", "Zenas"],
] as const;

export function Spark({ days }: { days: DailyValue[] }) {
  const points = sparkPoints(days.map(day => day.value));
  return <span className="control-spark">{points && <svg width="120" height="32" viewBox="0 0 120 32" aria-hidden="true">
    <polyline points={points} fill="none" stroke="#001840" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
  </svg>}</span>;
}

function Metric({ label, value, note, days, href }: { label: string; value: string; note?: React.ReactNode; days: DailyValue[]; href: string }) {
  return <section className="control-card control-metric">
    <p className="control-label">{label}</p>
    <a className="control-value" href={href}>{value}</a>
    <div className="control-metric-sub">{note && <a href={href}>{note}</a>}<a className="control-trend" href={href} aria-label={label}><Spark days={days} /></a></div>
  </section>;
}

export function WorkersTile({ workers, now }: { workers: WorkersRead | null | undefined; now?: number }) {
  const beat = workers?.worker ?? null;
  const live = checkingIn(beat, now);
  const status = !workers ? "Workers status unavailable" : !beat ? "No check-in yet" : !live ? "not checking in" : beat.state === "paused" ? "Paused" : beat.issue ? `Running · issue #${beat.issue}` : "Running · idle";
  return <section className="dash-card" data-tile="workers"><div className="dash-subhead"><h2>Workers</h2><span className="dash-note">Boston host · WORKERS-2</span></div>
    <div className="dash-row"><span>Status</span><span className={live ? "dash-chip" : "dash-problem"}>{status}</span></div>
    <div className="dash-row"><span>Commit</span><span className="dash-note">{beat ? beat.commit.slice(0, 12) : "—"}</span></div>
    <div className="dash-row"><span>Last check-in</span><span className="dash-note">{beat ? `${beat.seen_at} · ${beat.age_s}s ago` : "—"}</span></div>
    {beat?.last_error && <div className="dash-row"><span>Last error</span><span className="dash-note">{beat.last_error}</span></div>}
    <div className="dash-row"><span>{workers ? (workers.workers_paused ? "Workers are paused: no new task is taken" : "Workers take new tasks") : "Pause unavailable"}</span><WorkersActions paused={workers ? workers.workers_paused : null} /></div>
  </section>;
}

export function FounderDashboard({ view }: { view: FounderView }) {
  const { data, signups, paying, activity, now } = view;
  return <div className="control-panel">
    <header className="control-top">
      <img src="/brand/tervory-logo.png" alt="Tervory AI" width="180" height="33" />
      <h1>Control panel</h1>
      <ControlActions paused={view.paused} />
    </header>
    <div className="control-metrics">
      <Metric label="Sign-ups" value={count(signups.total)} note={<>+<span className="control-number">{signups.thisWeek}</span> this week</>} days={signups.days} href={sourceLink("signups")} />
      <Metric label="Paying clients" value={count(paying.total)} note={<>+<span className="control-number">{paying.thisWeek}</span> this week</>} days={paying.days} href={sourceLink("paying")} />
      <Metric label="Work done today" value={count(activity.delivered)} note={<><span className="control-number">{activity.codePercent}</span>% by code, no model</>} days={activity.work} href={sourceLink("activity", { day: new Date(now).toISOString().slice(0, 10) })} />
      <Metric label="Spent this month" value={money(activity.spent)} days={activity.spend} href={sourceLink("activity")} />
    </div>
    <NeedsYou skills={passedSkills(data.skills!).length} models={view.models} checks={view.checks} />
    <div className="control-bottom">
      <section className="control-card control-squad">
        <h2>Squad</h2>
        {squads.map(([agent, name]) => {
          const rows = data.squad!.filter(row => row.agent === agent);
          const running = rows.filter(row => row.status === "running").reduce((total, row) => total + wholeNumber(row.jobs), 0);
          const queued = rows.filter(row => row.status === "queued").reduce((total, row) => total + wholeNumber(row.jobs), 0);
          const dot = running ? greenDot : queued ? amberDot : greyDot;
          return <div className="control-squad-row" key={agent}>
            <img src={dot} alt="" width="10" height="10" />
            <p className="control-squad-name">{name}</p>
            <a className="control-squad-count" href={sourceLink("squad", { agent })}>{running ? <><span className="control-number">{running}</span> running</> : queued ? <><span className="control-number">{queued}</span> queued</> : <><span className="control-number">{running}</span> running</>}</a>
          </div>;
        })}
      </section>
      <section className="control-card control-signups">
        <div className="control-head"><h2>Latest sign-ups</h2><a className="control-button" href="/control/signups">All <span className="control-number">{signups.total}</span></a></div>
        {signups.recent.map(user => {
          const age = signupAge(user.createdAt, now);
          const parts = age.match(/^(\d+)(.*)$/);
          return <div className="control-signup-row" key={user.id}>
          <div className="control-signup-who"><p>{user.name}</p><p>{user.company} · {user.country}</p></div>
          <span className="control-plan">{user.plan}</span>
          <a className="control-age" href={sourceLink("signups", { user: user.id })}>{parts ? <><span className="control-number">{parts[1]}</span>{parts[2]}</> : age}</a>
        </div>;
        })}
      </section>
      <section className="control-card control-controls">
        <h2>Controls</h2>
        <div className="control-workers"><p>Squad workers</p><WorkersActions paused={view.workersPaused} /></div>
        <div className="control-quick"><a className="control-button primary" href="/chats">Open the window</a><a className="control-button" href="/tank/proof">Proof report</a></div>
      </section>
    </div>
  </div>;
}
