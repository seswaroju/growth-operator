// TX1 — ready-made automation examples + plain-language option docs, so an owner can learn the
// Automations builder by starting from a real one. Pure data (unit-tested); the builder loads a draft
// straight from `example.draft`. The server still validates + reviews before anything activates.

import type { WorkflowDraft } from "./workflows";

export type Complexity = "simple" | "medium" | "complex";

export interface AutomationExample {
  id: string;
  title: string;
  complexity: Complexity;
  summary: string; // what it does + why you'd use it
  draft: WorkflowDraft;
}

const V = 1;

export const AUTOMATION_EXAMPLES: AutomationExample[] = [
  // ---- Simple ----------------------------------------------------------------------------------
  {
    id: "welcome-new-lead",
    title: "Welcome a new enquiry",
    complexity: "simple",
    summary:
      "When someone new messages the store, send a warm welcome and ask what they're looking for. " +
      "One step — the fastest way to never leave a new enquiry waiting.",
    draft: {
      workflow: "welcome_new_lead", version: V, eventType: "lead.created", condition: "",
      steps: [
        { type: "agent_task", archetype: "nurture",
          task: "Send a warm welcome, thank them for reaching out, and ask what they're shopping for.",
          timeout: "2m" },
      ],
    },
  },
  {
    id: "quote-follow-up",
    title: "Follow up after a quote",
    complexity: "simple",
    summary:
      "After a customer is quoted, wait two days, then gently follow up and offer to answer questions. " +
      "Catches the quotes that go quiet without any manual chasing.",
    draft: {
      workflow: "quote_follow_up", version: V, eventType: "lead.stage.changed",
      condition: "payload.stage == 'quoted'",
      steps: [
        { type: "wait", waitFor: "duration", timeout: "48h" },
        { type: "agent_task", archetype: "nurture",
          task: "Follow up warmly on the quote, ask if they have questions, and offer to help decide." },
      ],
    },
  },
  // ---- Medium ----------------------------------------------------------------------------------
  {
    id: "reactivate-quiet-lead",
    title: "Re-open a conversation that went quiet",
    complexity: "medium",
    summary:
      "When a lead goes quiet, send a friendly check-in, wait for a reply for three days, and if they're " +
      "still silent, ask you to approve a stronger offer. Recovers stalled conversations with a safety check.",
    draft: {
      workflow: "reactivate_quiet_lead", version: V, eventType: "lead.went_quiet", condition: "",
      steps: [
        { type: "agent_task", archetype: "nurture",
          task: "Re-open the conversation with a light, friendly check-in — no pressure." },
        { type: "wait", waitFor: "reply", timeout: "72h" },
        { type: "human_task", kind: "approval", assignee: "role:owner" },
      ],
    },
  },
  {
    id: "festival-greeting",
    title: "Festival greeting with your approval",
    complexity: "medium",
    summary:
      "Tag a new lead into a festival segment, draft a seasonal greeting that shares the new collection, " +
      "and hold it for your approval before it sends. Timely outreach that never goes out unreviewed.",
    draft: {
      workflow: "festival_greeting", version: V, eventType: "lead.created", condition: "",
      steps: [
        { type: "set", varsJson: '{"segment": "festival"}' },
        { type: "agent_task", archetype: "nurture",
          task: "Wish them for the season and share highlights from the new collection." },
        { type: "human_task", kind: "approval", assignee: "role:owner" },
      ],
    },
  },
  // ---- Complex ---------------------------------------------------------------------------------
  {
    id: "ghost-recovery",
    title: "Ghost-recovery — diagnose, then win back",
    complexity: "complex",
    summary:
      "The core play: when a lead ghosts, first diagnose why, then compose a tailored win-back, hold it " +
      "for your approval, wait five days for a reply, and make one more attempt if needed.",
    draft: {
      workflow: "ghost_recovery", version: V, eventType: "lead.went_quiet", condition: "",
      steps: [
        { type: "agent_task", archetype: "diagnose",
          task: "Work out the most likely reason this lead went quiet (price, timing, trust, …)." },
        { type: "agent_task", archetype: "nurture",
          task: "Compose a warm win-back that speaks to that specific reason." },
        { type: "human_task", kind: "approval", assignee: "role:owner" },
        { type: "wait", waitFor: "reply", timeout: "120h" },
        { type: "agent_task", archetype: "nurture",
          task: "If still no reply, make one final, gentle attempt with a clear next step." },
      ],
    },
  },
  {
    id: "high-value-lead",
    title: "High-value lead — concierge handling",
    complexity: "complex",
    summary:
      "For leads above a value threshold, mark them high priority, open with a concierge-style intro, get " +
      "your approval, then follow up after two days. Gives your best prospects white-glove care.",
    draft: {
      workflow: "high_value_lead", version: V, eventType: "lead.stage.changed",
      condition: "payload.value_minor > 5000000",
      steps: [
        { type: "set", varsJson: '{"priority": "high"}' },
        { type: "agent_task", archetype: "nurture",
          task: "Introduce a dedicated, concierge-style point of contact and offer a private viewing." },
        { type: "human_task", kind: "approval", assignee: "role:owner" },
        { type: "wait", waitFor: "reply", timeout: "48h" },
        { type: "agent_task", archetype: "nurture",
          task: "Follow up personally, referencing what they were interested in." },
      ],
    },
  },
  {
    id: "post-purchase-review",
    title: "Post-purchase review & referral",
    complexity: "complex",
    summary:
      "A week after an order completes, ask for a review and a referral — with your approval on the " +
      "wording. Turns a happy purchase into word-of-mouth without you remembering to ask.",
    draft: {
      workflow: "post_purchase_review", version: V, eventType: "order.completed", condition: "",
      steps: [
        { type: "wait", waitFor: "duration", timeout: "168h" },
        { type: "agent_task", archetype: "nurture",
          task: "Thank them again, ask how they're enjoying the piece, and invite a review + referral." },
        { type: "human_task", kind: "approval", assignee: "role:owner" },
      ],
    },
  },
];

// ---- Option docs ("like documented script arguments") -----------------------------------------

export interface OptionDoc {
  name: string;
  what: string;
  why: string;
  how: string;
}

export const AUTOMATION_OPTIONS: OptionDoc[] = [
  {
    name: "Trigger event",
    what: "The thing that happens in your store that starts the automation.",
    why: "It decides when the automation runs — you pick the moment it should kick in.",
    how: "e.g. lead.created (a new enquiry), lead.stage.changed (they moved in your pipeline), " +
      "lead.went_quiet (they stopped replying), order.completed (a sale finished).",
  },
  {
    name: "Condition (optional)",
    what: "An optional check on the event's data — the automation only runs when it's true.",
    why: "Use it to narrow down: run only for quoted leads, or only above a value, etc.",
    how: "A short expression over `payload`, e.g. payload.stage == 'quoted' or payload.value_minor > 5000000. " +
      "Leave it blank to run every time the event happens.",
  },
  {
    name: "Step · Agent task",
    what: "The AI does one job — usually drafting a message grounded in your catalog and prices.",
    why: "This is where the actual outreach is written; it never invents products or prices.",
    how: "Give it an archetype (e.g. nurture, diagnose) and a plain task ('Follow up warmly on the quote'). " +
      "An optional timeout (e.g. 2m) caps how long it may take.",
  },
  {
    name: "Step · Wait",
    what: "Pauses the automation until something happens.",
    why: "Space out follow-ups, or wait to see if the customer replies before doing more.",
    how: "Choose for: reply (until they answer), duration (a fixed time), or event. Set a timeout like 48h or 3d.",
  },
  {
    name: "Step · Human approval",
    what: "Stops and asks a person to approve before the automation continues.",
    why: "Your safety net — nothing customer-facing goes out until someone signs off.",
    how: "Assign who approves (e.g. role:owner). The draft waits in your Approvals queue until you act.",
  },
  {
    name: "Step · Set variables",
    what: "Stores a little data the later steps can use.",
    why: "Tag or label a lead (e.g. mark a segment or priority) so downstream steps behave differently.",
    how: "A small JSON object, e.g. {\"segment\": \"festival\"} or {\"priority\": \"high\"}.",
  },
  {
    name: "Safety guards",
    what: "Server-locked rules that always apply (e.g. never message a suppressed or opted-out contact).",
    why: "They protect your customers and your compliance — they can't be turned off from here.",
    how: "Added automatically to every automation; you'll see them listed. Nothing you do can remove them.",
  },
];
