"""Fixed14 model inspector: what the shipped pipeline saw and weighed on one image.

It runs the unchanged shared pipeline (src.service.recognizer.Recognizer, F1v,
F8 and F9 included) and records every band BC-final read. For the band behind
the final reading it runs BC-final's own inspection graph, which shares the
production weights, and returns the per-digit probabilities, the weakest-position
margin, the 14 ordered-slot attention maps and the vertical row attention.

Nothing here edits, wraps or re-implements a sealed module: the recording is a
subclass of the service runtime that copies each BC-final input and output, and
a load-time self-check refuses to serve unless the inspection graph reproduces
the production logits on the sealed probe band.
"""
