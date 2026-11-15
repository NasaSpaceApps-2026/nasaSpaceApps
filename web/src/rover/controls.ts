/**
 * Keyboard drive input: W/S or Up/Down = forward/reverse, A/D or Left/Right =
 * turn left/right. Emits "takeover" on the first press of any drive key (not
 * on auto-repeat): per the API control rule, any key press switches the rover
 * to manual and cancels the active auto command.
 */

import type { DriveInput } from './physics';

const FORWARD = new Set(['KeyW', 'ArrowUp']);
const BACK = new Set(['KeyS', 'ArrowDown']);
const LEFT = new Set(['KeyA', 'ArrowLeft']);
const RIGHT = new Set(['KeyD', 'ArrowRight']);
const BOOST = new Set(['ShiftLeft', 'ShiftRight']);
const BRAKE = new Set(['Space']);
const DRIVE_KEYS = new Set([...FORWARD, ...BACK, ...LEFT, ...RIGHT]);
/** Time-lapse: ] faster, [ slower. */
const TIME_UP = 'BracketRight';
const TIME_DOWN = 'BracketLeft';

type Listener = () => void;

export interface KeyboardControlsOptions {
  /** Event target to listen on. Default: window. */
  target?: Window | HTMLElement;
  /** Call preventDefault on drive keys (stops arrow keys scrolling). Default true. */
  preventDefault?: boolean;
}

function isTyping(e: KeyboardEvent): boolean {
  const el = e.target as HTMLElement | null;
  if (!el || !el.tagName) return false;
  return el.isContentEditable || el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.tagName === 'SELECT';
}

export class KeyboardControls {
  enabled = true;
  private down = new Set<string>();
  private listeners = new Set<Listener>();
  private timeFns = new Set<(dir: 1 | -1) => void>();
  private target: Window | HTMLElement;
  private prevent: boolean;

  constructor(opts: KeyboardControlsOptions = {}) {
    this.target = opts.target ?? window;
    this.prevent = opts.preventDefault ?? true;
    this.target.addEventListener('keydown', this.onDown as EventListener);
    this.target.addEventListener('keyup', this.onUp as EventListener);
    window.addEventListener('blur', this.onBlur);
  }

  /** Subscribe to takeover events; returns an unsubscribe function. */
  onTakeover(fn: Listener): () => void {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }

  /** Subscribe to time-lapse key presses ([ = -1, ] = +1). */
  onTimeScale(fn: (dir: 1 | -1) => void): () => void {
    this.timeFns.add(fn);
    return () => this.timeFns.delete(fn);
  }

  /** Current input from held keys. */
  getInput(): DriveInput {
    if (!this.enabled) return { throttle: 0, steer: 0, boost: false, brake: false };
    const any = (set: Set<string>) => [...set].some((k) => this.down.has(k));
    return {
      throttle: (any(FORWARD) ? 1 : 0) - (any(BACK) ? 1 : 0),
      steer: (any(RIGHT) ? 1 : 0) - (any(LEFT) ? 1 : 0),
      boost: any(BOOST),
      brake: any(BRAKE),
    };
  }

  isDriving(): boolean {
    const i = this.getInput();
    return i.throttle !== 0 || i.steer !== 0;
  }

  dispose(): void {
    this.target.removeEventListener('keydown', this.onDown as EventListener);
    this.target.removeEventListener('keyup', this.onUp as EventListener);
    window.removeEventListener('blur', this.onBlur);
    this.listeners.clear();
    this.down.clear();
  }

  private onDown = (e: KeyboardEvent): void => {
    if (!this.enabled || isTyping(e) || e.ctrlKey || e.metaKey || e.altKey) return;
    if ((e.code === TIME_UP || e.code === TIME_DOWN) && !e.repeat) {
      for (const fn of this.timeFns) fn(e.code === TIME_UP ? 1 : -1);
      return;
    }
    if (BOOST.has(e.code) || BRAKE.has(e.code)) {
      if (this.prevent) e.preventDefault();
      this.down.add(e.code); // boost/brake alone is not a takeover
      return;
    }
    if (!DRIVE_KEYS.has(e.code)) return;
    if (this.prevent) e.preventDefault();
    const fresh = !this.down.has(e.code) && !e.repeat;
    this.down.add(e.code);
    if (fresh) for (const fn of this.listeners) fn();
  };

  private onUp = (e: KeyboardEvent): void => {
    this.down.delete(e.code);
  };

  private onBlur = (): void => {
    this.down.clear();
  };
}
