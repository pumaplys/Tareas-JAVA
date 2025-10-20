package tema2.t02;
// Muestra los números primos entre 1 y 100.
public class ej08 {
    public static void main(String[] args) {

        for (int i = 1; i <= 100; i++){
            if (i % 2 == 0){
                System.out.println(i);
            }
        }
    }
}
