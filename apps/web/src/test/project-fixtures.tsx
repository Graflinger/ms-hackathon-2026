import { type ReactNode } from "react";
import { vi } from "vitest";
import { createProjectApi, type Project, type Revision } from "../api";
import {
  ProjectProvider,
  DEMO_PROJECT,
  DEMO_AGENT,
} from "../project";
import { defaultSpec } from "../pages/Agents";

export const api = createProjectApi(DEMO_PROJECT);
export const project: Project = {
  id: DEMO_PROJECT,
  name: "Synthetic Demo",
  description: "Synthetic fixtures",
  archived: false,
  created_at: "2026-09-08",
};
export const agent = {
  ...project,
  id: DEMO_AGENT,
  project_id: project.id,
  name: "Synthetic Customer Lookup",
};
export const revision: Revision = {
  id: "registered-fixed",
  agent_id: agent.id,
  project_id: project.id,
  label: "Fixed",
  number: 2,
  spec: {
    ...defaultSpec,
    modes: ["mock", "live"],
    connection: {
      endpoint: "https://example.openai.azure.com",
      deployment: "demo",
      api_version: "2025-01-01",
      auth: "api_key",
      binding: "demo",
    },
  },
  spec_hash: "a".repeat(64),
  created_at: project.created_at,
  legacy: false,
  spec_provenance: "registered",
};
export const execution = {
  legacy: false,
  project_id: project.id,
  agent_id: agent.id,
  agent_name: agent.name,
  agent_revision_id: revision.id,
  agent_revision_label: revision.label,
  spec_hash: revision.spec_hash,
};
export function mockRegistry() {
  vi.spyOn(api, "agents").mockResolvedValue([agent]);
  vi.spyOn(api, "revisions").mockResolvedValue([revision]);
}
export function TestProject({ children }: { children: ReactNode }) {
  return (
    <ProjectProvider project={project} api={api}>
      {children}
    </ProjectProvider>
  );
}
