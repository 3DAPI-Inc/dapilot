# Art direction for one product

How to fill each field of `design/brief.json`. The goal of every field is the
same: a buyer scrolling Etsy understands what the object is, how big it is and
what they would do with it, and receives exactly what the photos showed.

## name

What a buyer would call it, in Title Case, 2 to 5 words, at most 60 characters.

- End on the product noun from `context.product.noun`, in its natural form
  ("Clicker", "Planter", "Cable Organizer").
- Add the one feature that tells it apart: the shape or theme ("Cat"), the
  mechanism ("Passthrough", "Articulated"), or the use ("Desk").
- Borrow wording from `comparable_titles` only where most of them agree.
- Not in the name: sizes, colours, "3D printed", "gift for", keyword lists,
  pipes or hashtags, the file name's designer or site, any brand or character
  you do not own (no Pokemon, Disney, Marvel, sports teams).

Good: `Cat Fidget Clicker`, `Octagon Passthrough Fidget`, `Low Succulent Planter`.
Bad: `Cute Cat Clicker Fidget Toy Gift ADHD`, `cat (clickerfactory.com)`.

## rotate and hero_yaw

The drafts show the file as exported, at yaw 0, 45, ... 315, camera 18 degrees
up. Same rules as the old render stage:

- **rotate** stands the object the way a buyer expects to meet it. A figure on
  its back, a vase on its side or a sign lying flat was exported in print
  orientation: `90,0,0` or `-90,0,0` usually fixes it, `0,90,0` for something
  lying on its flank. Most files need `0,0,0`. Flat objects that are used
  flat (coasters, trays, a clicker that sits on a desk) stay flat.
- **hero_yaw** is the draft yaw where the object reads best: the face of a
  figure, the front three-quarter of a functional part, the open side of a
  container, all parts visible for a multi-part object. Add 10 to 25 degrees
  to a dead-on view; a slight turn reads better than a flat elevation.

If you rotate, the drafts no longer show the result exactly. Choose yaw for
the side that will face the camera once standing; the review in step 6 checks it.

## angles

The stills after the hero, as yaws, at least 30 degrees from each other and
from the hero. Each must show something the hero does not: the back, the
side profile, the open mechanism, the other part. Hero plus 90, 180 and 270
is the default when nothing stands out.

## elevation

Camera height in degrees, 5 to 60. 18 suits upright objects. Raise it to 30
to 45 for flat or open objects whose top is the interesting part (trays,
containers, a clicker's face). Lower it to 10 for tall, slim objects.

## color

One of `context.palette`. Choose the colour the best-selling comparables are
shown in when their titles say so; otherwise a colour that shows the shape
against a light backdrop: warm white for detailed or organic shapes, a
saturated colour for simple, toy-like shapes, matte black for technical parts.

## lifestyle_images

Each is one photo of our product in a real setting, generated from one of our
stills (`reference`: `hero` or `angle_N`). The model keeps the product; you
describe everything around it. Plan them as a set, each answering a different
buyer question:

1. **In use**: where it lives. A desk, a kitchen counter, a shelf, a child's
   room, chosen from the comparables' titles.
2. **Scale**: next to one everyday object whose size nobody doubts (a coffee
   mug, a pencil, a keyboard, a smartphone), placed so the size reads.
3. **Mood**: the gift or the style shot, soft light, a tidy styled surface.

Write each scene in this order, in plain sentences, under 600 characters:

- the product, with its colour word from the palette and `context.size.scale`
  ("a palm-sized warm white 3D printed cat-shaped clicker"), and how many:
  always one
- where it stands and on what surface
- the light, and what is out of focus behind it
- the camera: "product photograph, shot on 85mm, sharp focus on the product"

Rules learned from the first fal runs:

- No people and no hands in still images. Hands come out with the wrong
  number of fingers often enough to cost a reject.
- Never ask for text, labels, packaging with writing, or a brand.
- One product. "Two" or "a set" makes the model invent a second one, and a
  set is a different listing (research already excludes sets).
- Say "matte" and "visible fine layer texture" for PLA; without it the model
  renders glossy injection-moulded plastic, which is not what ships.
- Choose `reference: angle_N` when that angle suits the scene better than the
  hero, for example a top-down scene from the high angle.

Leave the list empty when the product is a replacement part or a technical
component a buyer searches by fit, not by look.

## lifestyle_video

A 5 second clip generated on our own depth pass, so the geometry stays true
while Wan paints the setting. It becomes the listing video when it passes
review; the studio turntable is the fallback.

- `scene`: the same order as an image scene, for one setting, still
  and calm. No hands, no people, nothing moving but the product or the light.
- `motion`: `drift` (a slow 40 degree turn centred on the hero; the default,
  reads as a real camera) or `turn` (a full rotation in 5 seconds; only for
  objects whose back matters).
- `negative`: what must not appear. Start from
  `cgi, 3d render, video game, plastic sheen, oversaturated, text, watermark,
  deformed, extra objects, morphing background` and add the likely failure
  for this product (for a figure: `extra limbs`).

Set it to `null` when the product is technical, or when a lifestyle clip
would say nothing the turntable does not.

## reason

One or two sentences a person reads when checking the listing: why this side,
this colour, these scenes.
