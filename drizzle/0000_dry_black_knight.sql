CREATE TABLE `robot_events` (
	`id` integer PRIMARY KEY AUTOINCREMENT NOT NULL,
	`session_id` text NOT NULL,
	`kind` text NOT NULL,
	`role` text NOT NULL,
	`message` text NOT NULL,
	`latency_ms` integer,
	`created_at` integer NOT NULL
);
--> statement-breakpoint
CREATE INDEX `idx_robot_events_session_created` ON `robot_events` (`session_id`,`created_at`);
