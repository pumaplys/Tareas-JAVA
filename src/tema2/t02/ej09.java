package tema2.t02;
// Mostrar los N primeros términos de la serie de Fibonacci. N se definirá como una variable.
public class ej09 {
    public static void main(String[] args) {

        int N = 10;
        int a = 0;
        int b = 1;

        System.out.println("Los primeros " + N + " términos de la serie de Fibonacci son:");

        for (int i = 1; i <= N; i++) {
            System.out.print(a + " ");
            int siguiente = a + b;
            a = b;
            b = siguiente;
        }
    }
    }

