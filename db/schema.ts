import { index, integer, sqliteTable, text } from "drizzle-orm/sqlite-core";

export const robotEvents = sqliteTable("robot_events", {
  id: integer("id").primaryKey({ autoIncrement: true }),
  sessionId: text("session_id").notNull(),
  kind: text("kind", { enum: ["chat", "command", "error"] }).notNull(),
  role: text("role", { enum: ["you", "bot", "system"] }).notNull(),
  message: text("message").notNull(),
  latencyMs: integer("latency_ms"),
  createdAt: integer("created_at").notNull(),
}, (table) => [
  index("idx_robot_events_session_created").on(table.sessionId, table.createdAt),
]);
