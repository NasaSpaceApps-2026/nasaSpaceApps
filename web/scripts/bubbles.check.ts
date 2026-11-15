import assert from 'node:assert/strict'
import { BUBBLES, covers, localMeters } from '../src/bubbles.ts'

const jezero = BUBBLES.find((b) => b.id === 'crater')!
assert.equal(jezero.sizeM, 1800)
assert.equal(covers(jezero, jezero.lat, jezero.lon), true)
assert.equal(covers(jezero, 0, 0), false)
const atCenter = localMeters(jezero, jezero.lat, jezero.lon)
assert.ok(Math.abs(atCenter.x) < 1 && Math.abs(atCenter.y) < 1)

const olympus = BUBBLES.find((b) => b.id === 'olympus')!
assert.equal(covers(olympus, olympus.lat, olympus.lon), true)
assert.equal(covers(olympus, jezero.lat, jezero.lon), false)
console.log('bubbles ok')