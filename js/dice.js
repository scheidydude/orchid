/**
 * Multi Dice Roller - Dice Display Module
 * Handles rendering of dice faces and animations
 */

export class DiceDisplay {
    /**
     * @param {HTMLElement} container - The container element for dice display
     */
    constructor(container) {
        this.container = container;
        this.diceElements = [];
    }

    /**
     * Clear the dice display
     */
    clear() {
        this.container.innerHTML = '';
        this.diceElements = [];
    }

    /**
     * Render all dice with animation
     * @param {number[]} results - Array of dice values
     * @param {number} sides - Number of sides on each die
     * @returns {Promise<void>}
     */
    async renderDice(results, sides) {
        this.clear();

        // Create container for dice
        const diceContainer = document.createElement('div');
        diceContainer.className = 'dice-container';
        this.container.appendChild(diceContainer);

        // Create individual dice elements
        results.forEach((value, index) => {
            const diceElement = this.createDiceElement(value, sides, index);
            diceContainer.appendChild(diceElement);
            this.diceElements.push(diceElement);
        });

        // Animate dice appearance
        await this.animateDiceAppearance();
    }

    /**
     * Create a single dice element with CSS-based faces
     * @param {number} value - The dice value
     * @param {number} sides - Number of sides
     * @param {number} index - Dice index
     * @returns {HTMLElement}
     */
    createDiceElement(value, sides, index) {
        const dice = document.createElement('div');
        dice.className = `dice d${sides}`;
        dice.dataset.index = index;
        dice.dataset.value = value;
        dice.dataset.sides = sides;

        // Create dice face container
        const faceContainer = document.createElement('div');
        faceContainer.className = 'dice-face-container';

        // Create pip container for CSS-based pips
        const pipContainer = document.createElement('div');
        pipContainer.className = 'pip-container';

        // Add pips based on dice value
        const pipCount = Math.min(value, sides);
        for (let i = 0; i < pipCount; i++) {
            const pip = document.createElement('div');
            pip.className = 'pip';
            pipContainer.appendChild(pip);
        }

        // For dice with more than 20 sides, show numeric value
        if (sides > 20) {
            const valueSpan = document.createElement('span');
            valueSpan.className = 'dice-value';
            valueSpan.textContent = value;
            valueSpan.style.cssText = 'z-index: 1; font-size: 1.2rem; font-weight: 700;';
            faceContainer.appendChild(valueSpan);
        } else {
            faceContainer.appendChild(pipContainer);
        }

        dice.appendChild(faceContainer);

        return dice;
    }

    /**
     * Animate dice appearance with stagger effect
     * @returns {Promise<void>}
     */
    async animateDiceAppearance() {
        if (typeof anime === 'undefined') {
            // Fallback without animation
            this.diceElements.forEach((dice) => {
                dice.style.opacity = '1';
                dice.style.transform = 'scale(1)';
            });
            return;
        }

        // Staggered appearance animation
        return new Promise((resolve) => {
            anime({
                targets: this.diceElements,
                scale: [0, 1],
                opacity: [0, 1],
                rotate: () => anime.random(-30, 30),
                translateY: [50, 0],
                delay: anime.stagger(100, {start: 100}),
                easing: 'spring(1, 80, 10, 0)',
                duration: 600,
                complete: resolve
            });
        });
    }

    /**
     * Animate dice roll effect
     * @returns {Promise<void>}
     */
    async animateRollEffect() {
        if (typeof anime === 'undefined' || this.diceElements.length === 0) {
            return;
        }

        return new Promise((resolve) => {
            anime({
                targets: this.diceElements,
                scale: [1, 1.2, 1],
                rotate: () => anime.random(-180, 180),
                duration: 500,
                easing: 'easeInOutQuad',
                delay: anime.stagger(50),
                complete: resolve
            });
        });
    }

    /**
     * Highlight a specific dice
     * @param {number} index - Dice index to highlight
     */
    highlightDice(index) {
        if (this.diceElements[index]) {
            const dice = this.diceElements[index];
            
            if (typeof anime !== 'undefined') {
                anime({
                    targets: dice,
                    scale: [1, 1.3, 1],
                    boxShadow: ['0 4px 6px rgba(0,0,0,0.4)', '0 10px 30px rgba(99, 102, 241, 0.6)', '0 4px 6px rgba(0,0,0,0.4)'],
                    duration: 400,
                    easing: 'easeInOutQuad'
                });
            } else {
                dice.style.transform = 'scale(1.3)';
                setTimeout(() => {
                    dice.style.transform = '';
                }, 400);
            }
        }
    }

    /**
     * Get all dice elements
     * @returns {HTMLElement[]}
     */
    getDiceElements() {
        return this.diceElements;
    }

    /**
     * Update a specific dice value
     * @param {number} index - Dice index
     * @param {number} newValue - New value to display
     */
    updateDiceValue(index, newValue) {
        if (this.diceElements[index]) {
            const dice = this.diceElements[index];
            dice.dataset.value = newValue;
            
            const pipContainer = dice.querySelector('.pip-container');
            const valueSpan = dice.querySelector('.dice-value');
            const sides = parseInt(dice.dataset.sides);

            if (sides > 20) {
                if (valueSpan) {
                    valueSpan.textContent = newValue;
                }
            } else if (pipContainer) {
                pipContainer.innerHTML = '';
                const pipCount = Math.min(newValue, sides);
                for (let i = 0; i < pipCount; i++) {
                    const pip = document.createElement('div');
                    pip.className = 'pip';
                    pipContainer.appendChild(pip);
                }
            }
        }
    }
}
