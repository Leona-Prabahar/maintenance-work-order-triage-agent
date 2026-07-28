import type {
  AssignmentRequest,
  AssignmentResponse,
  Health,
  TriageResponse,
} from "./types";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, init);

  if (!res.ok) {
    let detail = `Request failed (${res.status})`;
    try {
      const body = await res.json();
      if (body?.detail) detail = body.detail;
    } catch {
      /* ignore body parse errors */
    }
    throw new Error(detail);
  }

  return (await res.json()) as T;
}

export function getHealth(): Promise<Health> {
  return request<Health>("/api/health");
}

/** Read the queue and classify it. Writes nothing. */
export function postTriage(): Promise<TriageResponse> {
  return request<TriageResponse>("/api/triage", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ include_assigned: false, limit: 100 }),
  });
}

/**
 * Approve one proposal and write the assignment.
 *
 * This is the ONLY call in the frontend that causes a write, and it is only
 * ever reached from a click on Approve.
 */
export function postAssignment(
  body: AssignmentRequest
): Promise<AssignmentResponse> {
  return request<AssignmentResponse>("/api/assignments", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}
