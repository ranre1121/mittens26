CREATE TABLE `job` (
	`id` text PRIMARY KEY NOT NULL,
	`status` text DEFAULT 'queued' NOT NULL,
	`filename` text NOT NULL,
	`content_type` text NOT NULL,
	`size_bytes` integer NOT NULL,
	`video_key` text NOT NULL,
	`verdict` text,
	`violation_count` integer,
	`result_key` text,
	`progress` integer DEFAULT 0 NOT NULL,
	`error` text,
	`attempts` integer DEFAULT 0 NOT NULL,
	`created_at` integer DEFAULT (cast(unixepoch('subsecond') * 1000 as integer)) NOT NULL,
	`claimed_at` integer,
	`finished_at` integer
);
--> statement-breakpoint
CREATE INDEX `job_status_created_idx` ON `job` (`status`,`created_at`);