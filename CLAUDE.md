## Instructions / Tenets
1. Write modern python compatible with 3.13
2. use modern tooling uv, ruff
3. Use types
4. Apply the following coding principles:
   4.1 Composed <m>ethod  
   4.2 Single Level of Abstraction
   4.3 Single Responsibility
5. Apply the following design principles:
   5.1 Side Effects at the Edge. 
        Example:A function that sends a notification inside business logic means every test, every agent run, and every dry run triggers a real notification. Move the side effect to the caller. The function computes what to send; the boundary layer sends it.
   5.2 Uncoupled Logic
        A function that imports a module to get its dependencies is married to that module. Pass dependencies as arguments instead. This lets swap implementations for testing without touching the import graph.
   5.3 Pure & Total Functions
        A function that throws on unexpected input is a function that’s lying about its return type. A total function handles every case.
   5.4 Explicit Data Flow
        When data moves through nested callbacks or mutates an object across multiple methods, we can’t follow the pipeline. Linear data flow, where each step takes input and returns output, is readable by both humans and machines.
  5.5 Replaceable by Value
        If you can swap a function call with its return value and the program still behaves the same, that function is referentially transparent. This property lets agents cache results, skip redundant computation, and reason about code by substitution.
