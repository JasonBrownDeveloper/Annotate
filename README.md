# Annotate
This is a code analysis tool to annotate assembled binary code and data.
The metadata is stored in a mysql style database, see included schema.sql.
Currently the only supported processor is the snes 65816 asm.
I've attempted to write it modularly enough to be able to easily add new languages and views.

This is a very early alpha.
I've only implemented features as I've needed them.

Binary data is stored in the bytes table.
Bytes that are code are marked via the codemap table.
Bytes that are data are marked via the datamap table.
The rest should be fairly self explanatory.
