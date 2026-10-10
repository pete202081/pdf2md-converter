export type JobStatus = 'QUEUED' | 'RUNNING' | 'SUCCEEDED' | 'PARTIAL' | 'FAILED';
export type StageStatus = 'PENDING' | 'RUNNING' | 'SUCCEEDED' | 'FAILED' | 'SKIPPED';

export interface Job {
  id: string;
  filename: string;
  status: JobStatus;
  page_count: number;
  chapter_count: number;
  created_at: string;
  updated_at: string;
  error: string | null;
  review_total?: number;
  review_pending?: number;
}

export interface Stage {
  id: number;
  stage_name: string;
  status: StageStatus;
  started_at: string | null;
  finished_at: string | null;
  error: string | null;
  review_total?: number;
  review_pending?: number;
  metrics: Record<string, unknown>;
}

export interface Chapter {
  order_index: number;
  title: string;
  folder_name: string;
  md_path: string | null;
  mermaid_count: number;
  image_count: number;
  word_count: number;
}

export interface JobDetail extends Job {
  stages: Stage[];
  chapters: Chapter[];
}

export interface ProgressEvent {
  job_id: string;
  stage: string;
  percent: number;
  message: string;
  ts: string | null;
  terminal: boolean;
}

export interface ApiErrorBody {
  error_code?: string;
  message?: string;
  correlation_id?: string | null;
  details?: Record<string, unknown>;
}

export interface DiagramReviewItem {
  id: string;
  chapter_order: number;
  page_number: number;
  occurrence_index?: number;
  asset_name: string;
  reason: string;
  decision: 'PENDING' | 'ACCEPTED_IMAGE' | 'EDITED_MERMAID';
  mermaid_code: string | null;
  reviewed_at: string | null;
}

export interface QualityFlag {
  id: number;
  page_number: number;
  chapter_order: number;
  code: string;
  message: string;
  reviewed_at: string | null;
}
export interface QualitySummary {
  inventory_ready: boolean;
  chapters: { order: number; source_pages: [number, number] | null }[];
  flags: QualityFlag[];
}
export interface EditableChapter { markdown: string; hash: string; can_undo: boolean }
