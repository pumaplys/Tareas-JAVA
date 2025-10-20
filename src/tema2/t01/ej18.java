package tema2.t01;
// Escribir un programa en Java que multiplique los 20 primeros números naturales (1*2*3*4*5…).
public class ej18 {
    public static void main(String[] args) {

        long resultado = 1;

        for (int i = 1; i <= 20; i++) {
            resultado *= i;
        }

        System.out.println("Escribir un programa en Java que multiplique los 20 primeros números naturales (1*2*3*4*5…)." + resultado);
    }
}
