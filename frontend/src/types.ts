export type Urgency = "safety_critical" | "production_stopping" | "routine";

/** Most severe first — the order rows are grouped in on the dashboard. */
export const URGENCY_ORDER: Urgency[] = [
  "safety_critical",
  "production_stopping",
  "routine",
];

export interface SafetySignal {
  category: string;
  category_label: string;
  label: string;
  matched_text: string;
}

export interface Crew {
  id: number;
  crew_code: string;
  name: string;
  specialty: string;
  shift: string;
  on_call: boolean;
}

export interface Assignment {
  assignment_id: number | null;
  crew_id: number;
  crew_code: string;
  crew_name: string;
  urgency: Urgency;
  rationale: string | null;
  proposed_by: "agent" | "human";
  approved_by: string;
  assigned_at: string | null;
}

export interface TriageProposal {
  work_order_id: number;
  work_order_number: string;
  machine_code: string;
  machine_name: string;
  machine_area: string;
  machine_criticality: string;
  reported_by: string;
  reporter_role: string | null;
  description: string;
  reported_at: string;
  status: string;

  urgency: Urgency;
  model_urgency: Urgency;
  safety_override: boolean;
  safety_signals: SafetySignal[];

  proposed_crew_id: number | null;
  proposed_crew_code: string | null;
  proposed_crew_name: string | null;
  rationale: string;

  awaiting_approval: boolean;
  assignment: Assignment | null;
}

export interface TriageResponse {
  generated_at: string;
  model: string;
  proposals: TriageProposal[];
  crews: Crew[];
  open_count: number;
  safety_count: number;
  notes: string[];
}

export interface AssignmentRequest {
  work_order_id: number;
  crew_id: number;
  urgency: Urgency;
  approved_by: string;
  rationale: string;
  proposed_by: "agent" | "human";
}

export interface AssignmentResponse {
  ok: boolean;
  assignment: Assignment | null;
  message: string;
}

export interface Health {
  status: string;
  mcp_connected: boolean;
  model: string;
  approval_gate_configured: boolean;
}
