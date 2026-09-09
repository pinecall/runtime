# Las tres regiones del prompt

> Por qué la view va al FINAL, dónde corta el código cada región, y qué enseña
> `--show-prompt`. La prueba de que el corte se sostiene es un test, no un ojo:
> `examples/clinica-norte/test/prompt-regions.test.ts`.

## La regla

Un prompt de Pinecall se manda siempre en este orden, y nunca en otro:

```
static   el prefijo fijo: quién es el agente, el conocimiento, las reglas, las tools
history  la conversación, y los resúmenes de los tramos que se colapsaron
dynamic  lo que la view dice del ahora: memoria, retrieval, y el estado de esta llamada
```

## Por qué la view va al final

Es la corrección que trajo NOOA (`docs/design/research-synthesis.md` §1 y la fila 5
del resumen: *paper 2607.20709 §context: static · events · dynamic*). La tentación
natural es la contraria — poner el estado arriba, donde el modelo lo lee primero,
"para que no se le escape". Y es exactamente lo que rompe la caché.

Lo que un proveedor cachea es un **prefijo**: la parte inicial del prompt que no
cambió respecto del turno anterior. La caché se corta en el primer byte distinto y
todo lo que viene después se vuelve a pagar. Un bloque que depende del estado puesto
arriba invalida el prefijo **en cada turno**, porque el estado cambia en cada turno:
identificas al paciente y el prompt entero deja de estar cacheado. Con el mismo
contenido puesto abajo, el prefijo — que en Clínica Norte son ~1.200 tokens de
docstring, conocimiento, reglas, protocolos y docs de tools — se cachea una vez y
sobrevive a la llamada entera.

De ahí sale el criterio 2 del hito, y de ahí que sea *byte a byte*: "casi igual" no
existe para una caché. Un espacio de más es un fallo de caché igual que un párrafo.

La consecuencia de diseño es la que manda en todo el resto del framework: **nada de
lo que hay en `static` puede leer el estado**. Las tools se declaran todas ahí, con
su docstring, visibles o no — la visibilidad (`when(state)`) viaja en la lista de
tools del request, no en el texto del prompt. Si `when` reescribiera el bloque
`<tools>`, cada `findPatient` que se apagara costaría el prefijo entero.

## Dónde corta el código

Todo está en `packages/pinecall/src/views/layout.ts`, una función por región:

| región | la escribe | qué mete |
|---|---|---|
| `static` | `staticRegion(agent)` | el docstring de la clase · `<!-- knowledge: … -->` · `<rules>` y `<protocols>` (de `views/lang.ts`, según el `language` del agente) · `<tools>` con una línea por tool declarada |
| `history` | `historyRegion(agent)` | los `collapse()`: por cada resumen, un `<!-- collapsed: {"seq":N} -->` y la frase. Los turnos los tiene el runtime; el framework sólo aporta lo que él mismo colapsó |
| `dynamic` | `dynamicRegion(agent, view)` | los marcadores `<!-- memory: … -->` y `<!-- retrieved: … -->` y el texto que la view devuelve para este estado |

`layout()` las devuelve como un objeto `Regions` de tres campos, nunca como una
cadena: quien manda el prompt manda tres piezas y el orden no es suyo. `render()`
es ese mismo `layout`, y es lo único que el bridge llama.

Las reglas y los protocolos son palabras del framework, idénticas en cada turno de
cada llamada — por eso están en `static`, y por eso `views/lang.ts` es una tabla por
idioma (`es`, `en`; `es` es el default y el fallback) y no una plantilla con huecos.

## Qué enseña `--show-prompt`

`showPrompt()` (en `views/render.ts`) imprime las tres regiones, cada una bajo su
cabecera, en su único orden:

```
── static ──
…
── history ──
…
── dynamic ──
…
```

Se ve en vivo con `pinecall run --show-prompt`, y offline —sin gateway, sin clave,
sin modelo— con:

```
cd examples/clinica-norte
pnpm exec pinecall prompt agent.ts --state test/prompts/states.json --case 1
```

`test/prompts/states.json` son tres estados: recién descolgado, identificado con
horas sobre la mesa, y reservado. `test/prompts/state-0|1|2.txt` son las tres
capturas, y `test/prompt-regions.test.ts` las vuelve a renderizar en cada `pnpm
test`: comprueba que la región estática es la misma cadena en los tres, que la
dinámica es distinta en los tres, que el orden impreso es el orden, y que las
capturas siguen siendo lo que el código produce hoy.

## Lo que NO prueba este corte

Que `book` no pueda ejecutarse sin el sí del paciente no es cosa del prompt. El
`confirm:` de la tool sólo viaja en la declaración (`side_effect: "irreversible"` +
la frase de lectura); la puerta vivía en el servidor y se retiró el 2026-09-06
— `docs/decisions/confirm.md` cuenta por qué y qué quedó en el cable. Un prompt
nunca fue un permiso, y hoy tampoco hay otro.
