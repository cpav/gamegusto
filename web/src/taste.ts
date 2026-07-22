/**
 * The cooking-themed taste vocabulary — one source of truth for the labels the
 * rating UI shows and the badges on library cards. The stored values are terse
 * enums (mirrors models/game_record.py); the emoji + hint are what make the
 * system explain itself, so nobody needs a manual to know what "bland" means.
 */

export type TasteVerdict = "chefs_kiss" | "hidden_gem" | "guilty_pleasure" | "bland" | "sent_back";
export type Course = "starter" | "main" | "dessert";

export interface TasteOption<T> {
  value: T;
  emoji: string;
  label: string;
  /** The one-line explanation, shown as the caption so the metaphor teaches itself. */
  hint: string;
}

/** How the game landed for you — your verdict, the critics aside. */
export const VERDICTS: TasteOption<TasteVerdict>[] = [
  { value: "chefs_kiss", emoji: "😋", label: "Chef's kiss", hint: "Loved it, whatever the reviews say" },
  { value: "hidden_gem", emoji: "💎", label: "Hidden gem", hint: "Underrated — more people should play it" },
  { value: "guilty_pleasure", emoji: "🍟", label: "Guilty pleasure", hint: "Not “good”, but I devoured it" },
  { value: "bland", emoji: "😐", label: "Bland", hint: "Looked delicious, left me cold" },
  { value: "sent_back", emoji: "🤢", label: "Sent it back", hint: "Bounced off / couldn’t finish" },
];

/** What it's for — how long a sit, and when you reach for it. */
export const COURSES: TasteOption<Course>[] = [
  { value: "starter", emoji: "🥗", label: "Starter", hint: "Quick bite, short sessions" },
  { value: "main", emoji: "🍖", label: "Main", hint: "The big sit-down" },
  { value: "dessert", emoji: "🍰", label: "Dessert", hint: "Cozy wind-down" },
];

export const verdictOf = (value: TasteVerdict | null): TasteOption<TasteVerdict> | null =>
  VERDICTS.find((option) => option.value === value) ?? null;

export const courseOf = (value: Course | null): TasteOption<Course> | null =>
  COURSES.find((option) => option.value === value) ?? null;
