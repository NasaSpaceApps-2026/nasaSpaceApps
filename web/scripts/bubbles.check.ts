import assert from 'node:assert/strict'
import { BUBBLES, covers, localMeters } from '../src/bubbles.ts'
import { dropOnBubbles } from '../src/pegman.ts'

const jezero = BUBBLES.find((b) => b.id === 'crater')!
assert.equal(jezero.sizeM, 1800)
assert.equal(covers(jezero, jezero.lat, jezero.lon), true)
assert.equal(covers(jezero, 0, 0), false)
const atCenter = localMeters(jezero, jezero.lat, jezero.lon)
assert.ok(Math.abs(atCenter.x) < 1 && Math.abs(atCenter.y) < 1)

const olympus = BUBBLES.find((b) => b.id === 'olympus')!
assert.equal(covers(olympus, olympus.lat, olympus.lon), true)
assert.equal(covers(olympus, jezero.lat, jezero.lon), false)

const hit = dropOnBubbles(18.4818, 77.4358)
assert.equal(hit?.world, 'crater')
assert.ok(hit && Math.abs(hit.x) < 1 && Math.abs(hit.y) < 1)
assert.equal(hit?.heading, 0)

const mons = dropOnBubbles(21.5548278127083, 222.31978845442714)
assert.equal(mons?.world, 'olympus')

assert.equal(dropOnBubbles(0, 0), null)

console.log('bubbles ok')