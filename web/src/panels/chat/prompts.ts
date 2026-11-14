export const DEMO_PROMPTS: string[] = [
  'Where are the best places to find carbonates near base?',
  'Plan a route between my two markers under 15°.',
  'Take me to the delta front.',
  'Plan a 4-hour Marswalk to the two best science targets.',
  'Scout that route; stop if unsafe and tell me why.',
  "What's worth sampling near me?",
];

/** Follow-up chips shown after an answer: the demo prompts minus the last one asked. */
export function followUps(lastUser: string | undefined, count = 3): string[] {
  const pool = DEMO_PROMPTS.filter((p) => p !== lastUser);
  const start = lastUser ? DEMO_PROMPTS.indexOf(lastUser) + 1 : 0;
  const rotated = [...pool.slice(Math.max(0, start - 1)), ...pool.slice(0, Math.max(0, start - 1))];
  return rotated.slice(0, count);
}
