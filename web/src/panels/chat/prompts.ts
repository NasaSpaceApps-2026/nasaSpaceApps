export const DEMO_PROMPTS: string[] = [
  // the 2-minute script beats (docs/demo-script.md)
  'Find 3 flat landing pads within a 30 minute walk of base.',
  'Where has carbonate been confirmed by the rover?',
  'Plan a walk from base to the Swift Run sample site and tell me whether the crew can always get back within a 4 hour EVA with 20% reserve.',
  'Take me to the delta front.',
  'Drive the buggy to the highest point within 1 km.',
];

/** Follow-up chips shown after an answer: the demo prompts minus the last one asked. */
export function followUps(lastUser: string | undefined, count = 3): string[] {
  const pool = DEMO_PROMPTS.filter((p) => p !== lastUser);
  const start = lastUser ? DEMO_PROMPTS.indexOf(lastUser) + 1 : 0;
  const rotated = [...pool.slice(Math.max(0, start - 1)), ...pool.slice(0, Math.max(0, start - 1))];
  return rotated.slice(0, count);
}
