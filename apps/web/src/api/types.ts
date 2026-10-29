export interface User {
  auth_mode?: "local" | "session";
  id: string;
  username: string;
  role: string;
}
export interface Project {
  id: string;
  name: string;
  brand: string;
  product: string;
  platform: string;
  phase: string;
  mode: string;
  revision: number;
  current_ruleset_id: string | null;
  created_at: string;
}
export interface Coverage {
  status: string;
  page?: number;
  sheet?: string;
  reason?: string;
  visual_review?: string;
  nonempty_cells?: number;
}
export interface Asset {
  id: string;
  project_id: string;
  kind: string;
  filename: string;
  mime: string;
  sha256: string;
  bytes: number;
  parse_status: string;
  coverage: Coverage[];
  metadata: {
    page_count?: number;
    duration_ms?: number;
    width?: number;
    height?: number;
    has_audio?: boolean;
    limits?: string[];
    human_coverage_ack?: { actor: string; note: string };
  };
  created_at: string;
}
export interface Locator {
  kind: string;
  page?: number;
  bbox?: number[];
  sheet?: string;
  cell?: string;
  image_key?: string;
  paragraph_index?: number;
  table_index?: number;
  row?: number;
  column?: number;
  line_start?: number;
  line_end?: number;
  merged_range?: string;
}
export interface Block {
  id: string;
  text: string;
  locator: Locator;
  extraction_method: string;
  coverage_status: string;
}
export interface SourceRef {
  block_id: string;
  quote: string;
}
export interface Rule {
  id: string;
  title: string;
  type: string;
  severity: string;
  scope: { platforms: string[]; phases: string[]; asset_kinds: string[] };
  predicate: {
    kind: string;
    instruction: string;
    terms: string[];
    window_ms: number[] | null;
    manual_required: boolean;
  };
  source_refs: SourceRef[];
  exceptions: string[];
  relations: string[];
  confirmation_status: string;
  confirmation_note: string;
}
export interface Conflict {
  id: string;
  rule_ids: string[];
  description: string;
  blocking: boolean;
  status: string;
  resolution: string;
}
export interface RuleSet {
  id: string;
  status: string;
  source_ids: string[];
  rules: Rule[];
  conflicts: Conflict[];
  revision: number;
  extraction_coverage: {
    asset_id: string;
    unit: string | number;
    status: string;
    limitations?: string[];
  }[];
  content_hash: string | null;
  confirmation_kind: string | null;
  created_at: string;
}
export interface Submission {
  id: string;
  creator: string;
  lineage_id: string;
  parent_id: string | null;
  version: number;
  script_id: string | null;
  video_id: string | null;
  cover_id: string | null;
  caption_id: string | null;
  pairing_confirmed: boolean;
  created_at: string;
}
export interface RunSummary {
  content_snapshot_id?: string;
  id: string;
  kind: string;
  status: string;
  assessment: string;
  freshness: string;
  submission_id: string | null;
  ruleset_id: string | null;
  created_at: string;
  error: {
    code: string;
    message: string;
    unknown_remote_state?: boolean;
  } | null;
}
export interface ProjectData {
  project: Project;
  assets: Asset[];
  rulesets: RuleSet[];
  submissions: Submission[];
  runs: RunSummary[];
  script_approvals: {
    id: string;
    script_id: string;
    ruleset_id: string;
    decision: string;
  }[];
}
export interface Config {
  text: { ready: boolean; missing: string[] };
  video: { ready: boolean; missing: string[] };
  models: { text: string | null; video: string | null };
  model_live_tested: boolean;
  limits: { max_bytes: number; max_duration_ms: number };
}
export interface Evidence {
  id: string;
  asset_id: string;
  channel: string;
  content: string;
  start_ms: number | null;
  end_ms: number | null;
  uncertainty_ms: number | null;
  source_block_id: string | null;
  origin: { source?: string; locator?: Locator };
}
export interface Check {
  rule_id: string;
  status: string;
  evidence_ids: string[];
  explanation: string;
  suggestion: string;
  limitations: string[];
}
export interface Stage {
  id: string;
  name: string;
  attempt: number;
  status: string;
  error: { message: string } | null;
  started_at: string | null;
  finished_at: string | null;
}
export interface Run extends RunSummary {
  ruleset_confirmation_kind: string | null;
  project_id: string;
  input_hash: string;
  input_snapshot: {
    verification_context_version?: number;
    product_context?: { brand: string; product: string };
    mode?: string;
    historical_trial?: boolean;
    trial_limits?: string[];
    allow_unverified_audio?: boolean;
    trial_note?: string;
  };
  revision: number;
  cancel_requested: boolean;
  rules: Rule[];
  checks: Check[];
  evidence: Evidence[];
  stages: Stage[];
  result: {
    mode?: string;
    limits?: string[];
    coverage?: { start_ms: number; end_ms: number; status: string }[];
    script_differences?: { description: string; evidence_ids: string[] }[];
  };
  assets: {
    id: string;
    kind: string;
    filename: string;
    sha256: string;
    metadata: Asset["metadata"];
  }[];
  decisions: {
    id: string;
    actor: string;
    decision: string;
    mode: string;
    reason: string;
    created_at: string;
  }[];
}
export interface WorkspaceProps {
  data: ProjectData;
  config: Config;
  busy: boolean;
  act: (work: () => Promise<unknown>, message?: string) => Promise<boolean>;
  onRun: (id: string) => void;
  onAsset: (asset: Asset, focusId?: string) => void;
}
