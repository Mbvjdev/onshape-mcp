# First Hermes prompts

Use the first prompt after `hermes mcp test onshape` succeeds. Start with read-only inspection, then use an explicitly disposable test document.

## 1. Confirm access without changing anything

```text
List my three most recently modified Onshape documents. Do not modify anything.
```

## 2. Inspect a known document before editing it

```text
Find my Onshape document named "<DOCUMENT NAME>". Inspect its Part Studios, parts, and feature tree. Do not create, modify, or delete anything. Summarize the available elements and identify any assumptions you would need confirmed before changing geometry.
```

## 3. Build a disposable test part

```text
Create an Onshape document named "Onshape MCP smoke test". Inspect its elements to find Part Studio 1. Create a TOP-plane sketch named "Base", add a circle at the origin with a 50 mm radius, then extrude it 10 mm as a new body. List the resulting parts. Convert all dimensions to meters when you call the tools.
```

## 4. Export after verification

```text
In the current test document, inspect the parts and feature tree first. If there is exactly one solid body matching the 100 mm diameter, 10 mm thick disc, export the Part Studio as an STL in millimeters to a temporary local path. Report the output path and file size.
```

## Good operating habits

- Ask for inspection before modification when using an existing design.
- State which document and Part Studio are in scope.
- Use a new test document while learning the workflow.
- Remember that tool dimensions are meters: 1 mm = 0.001 m.
- Let the rate limiter work. Do not hammer retry after a `429` response.
