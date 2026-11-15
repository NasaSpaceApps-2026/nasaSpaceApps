export const DEMO_PROMPTS: string[] = [
  'Find landing pads within a 30 minute walk of base.',
  'Where is the strongest carbonate evidence, and how confident is each site?',
  'Take me to the delta front.',
  'Drive the buggy to the highest point within 1 km.',
  'Is there line of sight from base to the delta front?',
  'When is the base lit between 07:00 and 17:00 local solar time?',
];

/** Follow-up chips shown after an answer: the demo prompts minus the last one asked. */
export function followUps(lastUser: string | undefined, count = 3): string[] {
  const pool = DEMO_PROMPTS.filter((p) => p !== lastUser);
  const start = lastUser ? DEMO_PROMPTS.indexOf(lastUser) + 1 : 0;
  const rotated = [...pool.slice(Math.max(0, start - 1)), ...pool.slice(0, Math.max(0, start - 1))];
  return rotated.slice(0, count);
}
